// SPDX-License-Identifier: Apache-2.0
// Package appraisal is an opt-in diagnostic, not the production GPU gate.
package appraisal

import (
	"bytes"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"reflect"
	"strings"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

const sdkSource = "11b2bfeb42a1158aa08744603ecb239ccb3d2265"
const limit = nftables.ReadbackLimit

var errRejected = errors.New("guest appraisal rejected")

// Same security predicates as host/attest-lab-gpus.py, checked by a parity test.
// SDK src/gpu/claims.cpp at sdkSource; absent positives are never synthesized.
var trueClaims = []string{
	"secboot", "x-nvidia-gpu-arch-check",
	"x-nvidia-gpu-attestation-report-cert-chain-fwid-match",
	"x-nvidia-gpu-attestation-report-parsed",
	"x-nvidia-gpu-attestation-report-nonce-match",
	"x-nvidia-gpu-attestation-report-signature-verified",
	"x-nvidia-gpu-driver-rim-fetched", "x-nvidia-gpu-driver-rim-signature-verified",
	"x-nvidia-gpu-driver-rim-version-match", "x-nvidia-gpu-driver-rim-measurements-available",
	"x-nvidia-gpu-vbios-rim-fetched", "x-nvidia-gpu-vbios-rim-signature-verified",
	"x-nvidia-gpu-vbios-rim-version-match", "x-nvidia-gpu-vbios-rim-measurements-available",
	"x-nvidia-gpu-vbios-index-no-conflict",
}

func object(raw []byte) (map[string]any, error) {
	v, err := nftables.Decode(raw) // Bounded depth, duplicates, UTF-8 and EOF.
	m, ok := v.(map[string]any)
	if err != nil || !ok {
		return nil, errRejected
	}
	return m, nil
}

func output(raw []byte, fields ...string) (map[string]any, error) {
	m, err := object(raw)
	if err != nil || len(m) != 2+len(fields) || m["result_code"] != json.Number("0") {
		return nil, errRejected
	}
	if message, ok := m["result_message"].(string); !ok || len(message) > 256 {
		return nil, errRejected
	}
	for _, field := range fields {
		if _, ok := m[field]; !ok {
			return nil, errRejected
		}
	}
	return m, nil
}

type device struct {
	serial      string
	certificate []byte
}

// Decode only identity/shape. Certificate and report authentication belong to
// the pinned SDK, not x509.ParseCertificate or the outer nonce string.
func evidence(raw []byte, nonce string) ([]byte, device, error) {
	row, err := output(raw, "evidences")
	rows, ok := row["evidences"].([]any)
	if err != nil || !ok || len(rows) != 1 {
		return nil, device{}, errRejected
	}
	m, ok := rows[0].(map[string]any)
	if !ok || len(m) != 4 || m["arch"] != "HOPPER" || m["nonce"] != nonce {
		return nil, device{}, errRejected
	}
	decode := func(key string) ([]byte, error) {
		s, ok := m[key].(string)
		if !ok || len(s) == 0 || len(s) > limit/2 {
			return nil, errRejected
		}
		b, err := base64.StdEncoding.Strict().DecodeString(s)
		if err != nil || len(b) == 0 || base64.StdEncoding.EncodeToString(b) != s {
			return nil, errRejected
		}
		return b, nil
	}
	if _, err := decode("evidence"); err != nil {
		return nil, device{}, err
	}
	chain, err := decode("certificate")
	if err != nil || len(chain) > 0x1400 || !bytes.HasPrefix(chain, []byte("-----BEGIN CERTIFICATE-----")) {
		return nil, device{}, errRejected
	}
	leaf, _ := pem.Decode(chain)
	if leaf == nil || leaf.Type != "CERTIFICATE" || len(leaf.Headers) != 0 {
		return nil, device{}, errRejected
	}
	cert, err := x509.ParseCertificate(leaf.Bytes)
	if err != nil || cert.SerialNumber.Sign() <= 0 || len(cert.SerialNumber.Bytes()) > 20 {
		return nil, device{}, errRejected
	}
	encoded, err := json.Marshal(rows)
	if err != nil || len(encoded) > limit {
		return nil, device{}, errRejected
	}
	return encoded, device{cert.SerialNumber.String(), chain}, nil
}

func claims(value any, nonce, serial string, now time.Time) (map[string]any, error) {
	rows, ok := value.([]any)
	if !ok || len(rows) != 1 {
		return nil, errRejected
	}
	m, ok := rows[0].(map[string]any)
	if !ok {
		return nil, errRejected
	}
	// Remote NRAS uses GH100; pinned SDK tests distinguish this from the
	// longer local certificate CN. This is not a capacity/SKU assertion.
	for name, want := range map[string]string{"eat_nonce": nonce, "ueid": serial,
		"hwmodel": "GH100", "x-nvidia-device-type": "gpu", "x-nvidia-gpu-claims-version": "4.0",
		"x-nvidia-gpu-driver-version": "595.91.07", "measres": "success", "dbgstat": "disabled"} {
		if m[name] != want {
			return nil, errRejected
		}
	}
	for _, name := range trueClaims {
		if m[name] != true {
			return nil, errRejected
		}
	}
	for _, name := range []string{"x-nvidia-mismatch-measurement-records", "x-nvidia-mismatch-opaque-data-records"} {
		v, present := m[name]
		list, ok := v.([]any)
		if !present || v != nil && (!ok || len(list) != 0) {
			return nil, errRejected
		}
	}
	for _, name := range []string{"attestation-report", "driver-rim", "vbios-rim"} {
		chain, ok := m["x-nvidia-gpu-"+name+"-cert-chain"].(map[string]any)
		if !ok || chain["x-nvidia-cert-status"] != "valid" || chain["x-nvidia-cert-ocsp-status"] != "good" {
			return nil, errRejected
		}
		value, ok := chain["x-nvidia-cert-expiration-date"].(string)
		expires, err := time.Parse(time.RFC3339Nano, value)
		if !ok || err != nil || !strings.HasSuffix(value, "Z") || !expires.After(now) {
			return nil, errRejected
		}
	}
	return m, nil
}

// Called only after the exact detached token has passed verify-token. This is
// decoding, not JWT authentication. Four SDK serialization defaults only.
func signedClaims(token any, nonce, serial string, now time.Time) error {
	rows, ok := token.([]any)
	if !ok || len(rows) != 2 {
		return errRejected
	}
	header, ok := rows[0].([]any)
	devices, devicesOK := rows[1].(map[string]any)
	if !ok || len(header) != 2 || header[0] != "JWT" || !devicesOK || len(devices) != 1 {
		return errRejected
	}
	if s, ok := header[1].(string); !ok || s == "" {
		return errRejected
	}
	for _, value := range devices {
		s, ok := value.(string)
		if !ok || len(s) > limit {
			return errRejected
		}
		parts := strings.Split(s, ".")
		if len(parts) != 3 || parts[0] == "" || parts[2] == "" {
			return errRejected
		}
		payload, err := base64.RawURLEncoding.Strict().DecodeString(parts[1])
		if err != nil {
			return errRejected
		}
		m, err := object(payload)
		if err != nil {
			return errRejected
		}
		for key, value := range map[string]any{"x-nvidia-device-type": "gpu", "x-nvidia-gpu-claims-version": "4.0",
			"x-nvidia-mismatch-measurement-records": nil, "x-nvidia-mismatch-opaque-data-records": nil} {
			if _, exists := m[key]; !exists {
				m[key] = value
			}
		}
		if _, err := claims([]any{m}, nonce, serial, now); err != nil {
			return err
		}
	}
	return nil
}

func verified(attested, checked map[string]any, nonce, serial string, now time.Time) error {
	a, err := claims(attested["claims"], nonce, serial, now)
	if err != nil {
		return err
	}
	b, err := claims(checked["claims"], nonce, serial, now)
	if err != nil || !reflect.DeepEqual(a, b) {
		return errRejected
	}
	return signedClaims(attested["detached_eat"], nonce, serial, now)
}

// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"strings"
	"testing"
	"time"
)

const canary = "PRIVATE-CANARY-DO-NOT-LOG"

func encoded(t *testing.T, v any) []byte {
	t.Helper()
	b, e := json.Marshal(v)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func validClaims(nonce, serial string) map[string]any {
	m := map[string]any{"eat_nonce": nonce, "ueid": serial, "hwmodel": "GH100", "x-nvidia-device-type": "gpu",
		"x-nvidia-gpu-claims-version": "4.0", "x-nvidia-gpu-driver-version": "595.91.07", "measres": "success", "dbgstat": "disabled",
		"x-nvidia-mismatch-measurement-records": nil, "x-nvidia-mismatch-opaque-data-records": nil}
	for _, name := range trueClaims {
		m[name] = true
	}
	for _, name := range []string{"attestation-report", "driver-rim", "vbios-rim"} {
		m["x-nvidia-gpu-"+name+"-cert-chain"] = map[string]any{"x-nvidia-cert-status": "valid", "x-nvidia-cert-ocsp-status": "good", "x-nvidia-cert-expiration-date": "2099-01-01T00:00:00Z"}
	}
	return m
}
func syntheticToken(t *testing.T, m map[string]any) any {
	return []any{[]any{"JWT", "synthetic-overall"}, map[string]any{"synthetic-device": "e30." + base64.RawURLEncoding.EncodeToString(encoded(t, m)) + ".synthetic-signature"}}
}
func certPEM(t *testing.T, serial int64) []byte {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(serial), NotBefore: time.Unix(0, 0), NotAfter: time.Date(2099, 1, 1, 0, 0, 0, 0, time.UTC)}
	raw, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: raw})
}
func evidenceRow(nonce string, cert []byte) map[string]any {
	return map[string]any{"arch": "HOPPER", "nonce": nonce, "evidence": base64.StdEncoding.EncodeToString([]byte("synthetic-not-real-evidence")), "certificate": base64.StdEncoding.EncodeToString(cert)}
}
func success(fields map[string]any) map[string]any {
	m := map[string]any{"result_code": 0, "result_message": "synthetic"}
	for k, v := range fields {
		m[k] = v
	}
	return m
}

func TestClaimsRejectEveryMissingOrFalsePositive(t *testing.T) {
	nonce := strings.Repeat("ab", 32)
	if _, err := claims([]any{validClaims(nonce, "1")}, nonce, "1", time.Now()); err != nil {
		t.Fatal(err)
	}
	for _, field := range trueClaims {
		for _, value := range []any{nil, false, 1, "true", []any{true}} {
			m := validClaims(nonce, "1")
			m[field] = value
			if _, err := claims([]any{m}, nonce, "1", time.Now()); err == nil {
				t.Fatalf("accepted %s=%v", field, value)
			}
		}
		m := validClaims(nonce, "1")
		delete(m, field)
		if _, err := claims([]any{m}, nonce, "1", time.Now()); err == nil {
			t.Fatal(field)
		}
	}
	for _, field := range []string{"eat_nonce", "ueid", "hwmodel", "measres", "dbgstat", "x-nvidia-device-type", "x-nvidia-gpu-claims-version", "x-nvidia-gpu-driver-version"} {
		for _, value := range []any{nil, canary, []any{}, map[string]any{}} {
			m := validClaims(nonce, "1")
			m[field] = value
			if _, err := claims([]any{m}, nonce, "1", time.Now()); err == nil || strings.Contains(err.Error(), canary) {
				t.Fatal(field)
			}
		}
	}
}

func TestCertificateRevocationExpiryAndInventory(t *testing.T) {
	for _, name := range []string{"attestation-report", "driver-rim", "vbios-rim"} {
		for field, values := range map[string][]any{"x-nvidia-cert-status": {nil, "revoked"}, "x-nvidia-cert-ocsp-status": {nil, "unknown", "revoked"}, "x-nvidia-cert-expiration-date": {nil, "2020-01-01T00:00:00Z", "2099-01-01T00:00:00+00:00", canary}} {
			for _, value := range values {
				m := validClaims("nonce", "1")
				m["x-nvidia-gpu-"+name+"-cert-chain"].(map[string]any)[field] = value
				if _, err := claims([]any{m}, "nonce", "1", time.Now()); err == nil {
					t.Fatal(name, field)
				}
			}
		}
	}
	for _, v := range []any{nil, []any{}, []any{validClaims("nonce", "1"), validClaims("nonce", "1")}} {
		if _, err := claims(v, "nonce", "1", time.Now()); err == nil {
			t.Fatal("bad count accepted")
		}
	}
	for _, field := range []string{"x-nvidia-mismatch-measurement-records", "x-nvidia-mismatch-opaque-data-records"} {
		for _, value := range []any{false, "", []any{1}} {
			m := validClaims("nonce", "1")
			m[field] = value
			if _, err := claims([]any{m}, "nonce", "1", time.Now()); err == nil {
				t.Fatal(field)
			}
		}
	}
}

func TestSignedDefaultsDoNotSupplySecurityPositives(t *testing.T) {
	defaults := []string{"x-nvidia-device-type", "x-nvidia-gpu-claims-version", "x-nvidia-mismatch-measurement-records", "x-nvidia-mismatch-opaque-data-records"}
	for mask := 0; mask < 16; mask++ {
		m := validClaims("nonce", "1")
		for i, k := range defaults {
			if mask&(1<<i) != 0 {
				delete(m, k)
			}
		}
		before := string(encoded(t, m))
		if signedClaims(syntheticToken(t, m), "nonce", "1", time.Now()) != nil {
			t.Fatal(mask)
		}
		if before != string(encoded(t, m)) {
			t.Fatal("mutated signed input")
		}
	}
	for _, field := range trueClaims {
		m := validClaims("nonce", "1")
		delete(m, field)
		if signedClaims(syntheticToken(t, m), "nonce", "1", time.Now()) == nil {
			t.Fatal(field)
		}
	}
	for _, field := range defaults {
		m := validClaims("nonce", "1")
		m[field] = canary
		if signedClaims(syntheticToken(t, m), "nonce", "1", time.Now()) == nil {
			t.Fatal(field)
		}
	}
}

func TestEvidenceClosedShapeAndCanonicalEncoding(t *testing.T) {
	nonce := strings.Repeat("cd", 32)
	cert := certPEM(t, 1)
	raw := encoded(t, success(map[string]any{"evidences": []any{evidenceRow(nonce, cert)}}))
	if _, d, err := evidence(raw, nonce); err != nil || d.serial != "1" {
		t.Fatal(err)
	}
	for _, field := range []string{"arch", "nonce", "evidence", "certificate", "extra"} {
		row := evidenceRow(nonce, cert)
		row[field] = canary
		if _, _, err := evidence(encoded(t, success(map[string]any{"evidences": []any{row}})), nonce); err == nil {
			t.Fatal(field)
		}
	}
	for _, rows := range []any{nil, []any{}, []any{evidenceRow(nonce, cert), evidenceRow(nonce, cert)}} {
		if _, _, err := evidence(encoded(t, success(map[string]any{"evidences": rows})), nonce); err == nil {
			t.Fatal("count")
		}
	}
	for _, raw := range [][]byte{[]byte(`{"result_code":0,"result_code":0,"result_message":"","claims":[]}`), []byte(`{"result_code":0.0,"result_message":"","claims":[]}`), []byte(`{"result_code":true,"result_message":"","claims":[]}`), []byte(`{} {}`), []byte(strings.Repeat("[", 18) + strings.Repeat("]", 18)), make([]byte, limit+1)} {
		if _, err := output(raw, "claims"); err == nil {
			t.Fatal("malformed JSON accepted")
		}
	}
}

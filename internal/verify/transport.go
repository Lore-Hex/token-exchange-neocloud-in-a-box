// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

type Observation struct {
	Response protocol.Response
	Nonce    [32]byte
	SPKI     [32]byte
}

// Fetch retrieves untrusted evidence only. It sends no inference, credentials,
// cookies or prompts. Check must authenticate it before trusting the endpoint.
func Fetch(ctx context.Context, endpoint string) (Observation, error) {
	var observed Observation
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.RawFragment != "" || u.RawPath != "" || (u.Path != "" && u.Path != "/") {
		return observed, errors.New("endpoint must be an HTTPS origin without credentials, path or query")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if _, err := rand.Read(observed.Nonce[:]); err != nil {
		return Observation{}, errors.New("cannot generate attestation nonce")
	}
	port := u.Port()
	if port == "" {
		port = "443"
	}
	// PKI is intentionally not the trust root here: this connection fetches
	// evidence binding its exact key to an independently verified TDX quote.
	dialer := tls.Dialer{NetDialer: &net.Dialer{Timeout: 10 * time.Second}, Config: &tls.Config{MinVersion: tls.VersionTLS13, InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}}}
	raw, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(u.Hostname(), port))
	if err != nil {
		return Observation{}, errors.New("attestation TLS connection failed")
	}
	defer raw.Close()
	stop := context.AfterFunc(ctx, func() { _ = raw.Close() })
	defer stop()
	deadline, _ := ctx.Deadline()
	if raw.SetDeadline(deadline) != nil {
		return Observation{}, errors.New("cannot bound attestation connection")
	}
	conn := raw.(*tls.Conn)
	state := conn.ConnectionState()
	if len(state.PeerCertificates) == 0 || (state.NegotiatedProtocol != "" && state.NegotiatedProtocol != "http/1.1") {
		return Observation{}, errors.New("invalid attestation TLS peer")
	}
	observed.SPKI = sha256.Sum256(state.PeerCertificates[0].RawSubjectPublicKeyInfo)
	u.Path = protocol.Path
	u.RawQuery = "nonce=" + hex.EncodeToString(observed.Nonce[:])
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return Observation{}, errors.New("invalid attestation request")
	}
	req.Close = true
	req.Header.Set("Accept", "application/json")
	if req.Write(conn) != nil {
		return Observation{}, errors.New("attestation request failed")
	}
	reader, err := boundedResponseReader(conn)
	if err != nil {
		return Observation{}, err
	}
	resp, err := http.ReadResponse(bufio.NewReader(reader), req)
	if err != nil {
		return Observation{}, errors.New("invalid attestation HTTP response")
	}
	defer func() { _ = conn.Close(); _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return Observation{}, errors.New("attestation endpoint did not return success")
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, (16<<20)+1))
	if err != nil || len(body) > 16<<20 {
		return Observation{}, errors.New("invalid or oversized attestation response")
	}
	if err := decodeResponse(body, &observed.Response); err != nil {
		return Observation{}, err
	}
	return observed, nil
}

// Bound HTTP headers separately without losing body bytes already buffered.
func boundedResponseReader(conn io.Reader) (io.Reader, error) {
	reader := bufio.NewReader(io.LimitReader(conn, (64<<10)+1))
	var header []byte
	for {
		line, err := reader.ReadSlice('\n')
		header = append(header, line...)
		if len(header) > 64<<10 || (err != nil && err != bufio.ErrBufferFull) {
			return nil, errors.New("invalid or oversized attestation headers")
		}
		if err == nil && (bytes.Equal(line, []byte("\r\n")) || bytes.Equal(line, []byte("\n"))) {
			break
		}
	}
	return io.MultiReader(bytes.NewReader(header), reader, conn), nil
}

func decodeResponse(body []byte, out *protocol.Response) error {
	if trimmed := bytes.TrimSpace(body); len(trimmed) == 0 || trimmed[0] != '{' {
		return errors.New("attestation JSON must be an object")
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	if uniqueJSON(d, 0) != nil {
		return errors.New("invalid attestation JSON structure")
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing attestation JSON")
	}
	d = json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(out) != nil {
		return errors.New("invalid attestation response fields")
	}
	return nil
}

func uniqueJSON(d *json.Decoder, depth int) error {
	if depth > 16 {
		return errors.New("JSON depth limit")
	}
	token, err := d.Token()
	if err != nil {
		return err
	}
	switch token {
	case json.Delim('{'):
		seen := make(map[string]bool)
		for d.More() {
			key, err := d.Token()
			s, ok := key.(string)
			if err != nil || !ok || !wireField(s) || seen[s] || len(seen) >= 64 {
				return errors.New("duplicate or excessive JSON fields")
			}
			seen[s] = true
			if err := uniqueJSON(d, depth+1); err != nil {
				return err
			}
		}
		end, err := d.Token()
		if err != nil || end != json.Delim('}') {
			return errors.New("invalid JSON object")
		}
	case json.Delim('['):
		count := 0
		for d.More() {
			count++
			if count > 64 {
				return errors.New("JSON array limit")
			}
			if err := uniqueJSON(d, depth+1); err != nil {
				return err
			}
		}
		end, err := d.Token()
		if err != nil || end != json.Delim(']') {
			return errors.New("invalid JSON array")
		}
	default:
		if _, ok := token.(json.Delim); ok {
			return errors.New("invalid JSON delimiter")
		}
	}
	return nil
}

// encoding/json accepts case-insensitive and Unicode-folded struct aliases.
// The protocol uses only lowercase ASCII snake_case; reject aliases before
// typed decoding so two spellings cannot overwrite the same security field.
func wireField(s string) bool {
	if len(s) == 0 || len(s) > 64 {
		return false
	}
	for _, c := range s {
		if (c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '_' {
			return false
		}
	}
	return true
}

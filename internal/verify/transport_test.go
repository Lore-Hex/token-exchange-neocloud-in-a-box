// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

func TestFetchUsesOneTLSConnectionAndFreshNonces(t *testing.T) {
	var calls, connections atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != protocol.Path || r.Method != "GET" || r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.ContentLength != 0 || len(r.URL.Query()) != 1 {
			t.Error("unexpected request data")
		}
		if nonce, err := hex.DecodeString(r.URL.Query().Get("nonce")); err != nil || len(nonce) != 32 {
			t.Error("invalid fresh nonce")
		}
		_ = json.NewEncoder(w).Encode(protocol.Response{Protocol: protocol.Name, Nonce: r.URL.Query().Get("nonce")})
	}))
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.StartTLS()
	defer server.Close()
	t.Setenv("HTTPS_PROXY", "http://127.0.0.1:1")
	first, err := Fetch(context.Background(), server.URL)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Fetch(context.Background(), server.URL)
	if err != nil {
		t.Fatal(err)
	}
	expected := sha256.Sum256(server.Certificate().RawSubjectPublicKeyInfo)
	if first.SPKI != expected || second.SPKI != expected || first.Nonce == second.Nonce || first.Response.Nonce != hex.EncodeToString(first.Nonce[:]) || calls.Load() != 2 || connections.Load() != 2 {
		t.Fatal("evidence was not fetched once on each fresh bound connection")
	}
}

func TestFetchNeverFollowsRedirects(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls.Add(1) }))
	defer target.Close()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
	defer server.Close()
	if _, err := Fetch(context.Background(), server.URL); err == nil || targetCalls.Load() != 0 {
		t.Fatal("redirect was accepted or followed")
	}
}

func TestFetchCancellationClosesSilentConnection(t *testing.T) {
	entered := make(chan struct{})
	finish := make(chan struct{})
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		select {
		case <-r.Context().Done():
		case <-finish:
		}
	}))
	defer server.Close()
	defer close(finish)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := Fetch(ctx, server.URL); done <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("request did not arrive")
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled fetch passed")
		}
	case <-time.After(time.Second):
		t.Fatal("cancellation did not close connection")
	}
}

func TestFetchRejectsUntrustedResponses(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		status     int
	}{
		{"malformed", "secret-invalid-body", 200},
		{"trailing", "{} {}", 200},
		{"unknown", `{"secret-field":"secret-value"}`, 200},
		{"duplicate", `{"protocol":"a","protocol":"b"}`, 200},
		{"case-alias", `{"protocol":"a","Protocol":"b"}`, 200},
		{"unicode-alias", `{"workload":{"stack_version":"a","\u017ftack_version":"b"}}`, 200},
		{"null", `null`, 200},
		{"oversized", strings.Repeat("x", (16<<20)+1), 200},
		{"server-error", "secret-upstream-error", 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer server.Close()
			_, err := Fetch(context.Background(), server.URL)
			if err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatalf("untrusted response accepted or leaked: %v", err)
			}
		})
	}
}

func TestFetchRejectsUnsafeOrigins(t *testing.T) {
	for _, endpoint := range []string{"http://localhost", "https://user:secret@localhost", "https://localhost/v1", "https://localhost?secret=1", "https://localhost?", "https://localhost#secret", "https://localhost/%2f", "not-a-url"} {
		if _, err := Fetch(context.Background(), endpoint); err == nil || strings.Contains(err.Error(), "secret") {
			t.Fatalf("unsafe origin accepted or leaked: %v", err)
		}
	}
}

func TestBoundedHeaderReaderPreservesBufferedBody(t *testing.T) {
	for _, size := range []int{1, 10000, 100000} {
		body := strings.Repeat("b", size)
		header := "HTTP/1.1 200 OK\r\nX-Test: value\r\n\r\n"
		r, err := boundedResponseReader(strings.NewReader(header + body))
		if err != nil {
			t.Fatal(err)
		}
		got, err := io.ReadAll(r)
		if err != nil || string(got) != header+body {
			t.Fatal("header/body boundary lost bytes")
		}
	}
	if _, err := boundedResponseReader(strings.NewReader("HTTP/1.1 200 OK\r\nX-Test: " + strings.Repeat("x", 64<<10) + "\r\n\r\n")); err == nil {
		t.Fatal("oversized HTTP headers accepted")
	}
	for _, suffix := range []string{"\r\n", "\n"} {
		first := "X-Test: " + strings.Repeat("x", 4096-len("X-Test: ")) + suffix
		second := "Y-Test: " + strings.Repeat("y", 64<<10) + "\r\n\r\n"
		if _, err := boundedResponseReader(strings.NewReader("HTTP/1.1 200 OK\r\n" + first + second)); err == nil {
			t.Fatal("fragment-aligned header terminator bypassed the header limit")
		}
		body := "body"
		valid := "HTTP/1.1 200 OK\r\n" + first + "\r\n" + body
		reader, err := boundedResponseReader(strings.NewReader(valid))
		if err != nil {
			t.Fatal(err)
		}
		got, err := io.ReadAll(reader)
		if err != nil || string(got) != valid {
			t.Fatal("valid long header lost body bytes")
		}
	}
}

func FuzzAttestationJSON(f *testing.F) {
	f.Add([]byte(`{"protocol":"tr-attestation/v0","tee":"tdx"}`))
	f.Add([]byte(`{"gpu_evidence":[]}`))
	f.Fuzz(func(t *testing.T, b []byte) {
		if len(b) > 1<<20 {
			return
		}
		var response protocol.Response
		_ = decodeResponse(b, &response)
	})
}

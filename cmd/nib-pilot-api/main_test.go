// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/pilotapi"
)

func TestCLIRequiresExplicitProviderConfiguration(t *testing.T) {
	keyFile := filepath.Join(t.TempDir(), "api-key")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("synthetic-test-", 5)+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	base := []string{"--api-key-file", keyFile, "--input-price", "0.10", "--output-price", "0.20"}
	cutoff := time.Now().Add(time.Hour).UTC().Format(time.RFC3339)
	for _, public := range []bool{false, true} {
		args := append([]string{}, base...)
		if public {
			args = append(args, "--public", "--retirement-at", cutoff)
		}
		if _, err := configuredHandler(args); err == nil {
			t.Fatalf("missing provider config accepted (public=%v)", public)
		}
		if _, err := configuredHandler(append(args, "--provider-config", "/nonexistent/provider.json")); err == nil {
			t.Fatal("missing provider file accepted")
		}
	}
	for _, id := range []string{"operator-a", "operator-b"} {
		provider := pilotapi.ProviderConfig{ID: id, StatusURL: "https://status." + id + ".org/healthz",
			SupportContact: "mailto:support@" + id + ".org", IncidentContact: "mailto:ops@" + id + ".org",
			Regions: []string{"declared-region"}}
		data, _ := json.Marshal(provider)
		path := filepath.Join(t.TempDir(), "provider.json")
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		args := append(append([]string{}, base...), "--provider-config", path)
		for _, public := range []bool{false, true} {
			mode := append([]string{}, args...)
			if public {
				mode = append(mode, "--public", "--retirement-at", cutoff)
			}
			handler, err := configuredHandler(mode)
			if err != nil {
				t.Fatal(err)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest("GET", "/v1/models", nil))
			if response.Code != 200 || !strings.Contains(response.Body.String(), `"id":"`+id+`"`) {
				t.Fatal("CLI did not load explicit operator")
			}
		}
		if _, err := configuredHandler(append(args, "--public")); err == nil {
			t.Fatal("public mode accepted no absolute retirement")
		}
		if _, err := configuredHandler(append(args, "--public", "--retirement-at", "2000-01-01T00:00:00Z")); err == nil {
			t.Fatal("expired public restart accepted")
		}
		if err := os.WriteFile(path, []byte(`{"id":"missing-fields"}`), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := configuredHandler(args); err == nil {
			t.Fatal("CLI bypasses strict provider validation")
		}
	}
	args := append(append([]string{}, base...), "--provider-config", "../../examples/provider.json")
	if _, err := configuredHandler(args); err != nil {
		t.Fatalf("local example rejected: %v", err)
	}
	if _, err := configuredHandler(append(args, "--public", "--retirement-at", cutoff)); err == nil {
		t.Fatal("public mode accepted the onboarding example")
	}
}

func TestCLIKeyFileLengthAndNewline(t *testing.T) {
	keyFile := filepath.Join(t.TempDir(), "api-key")
	args := []string{"--api-key-file", keyFile, "--provider-config", "../../examples/provider.json",
		"--input-price", "0.10", "--output-price", "0.20"}
	for _, size := range []int{32, 63, 64, 256, 257} {
		for _, suffix := range []string{"", "\n", "\r\n", "\n\n"} {
			if err := os.WriteFile(keyFile, []byte(strings.Repeat("x", size)+suffix), 0600); err != nil {
				t.Fatal(err)
			}
			_, err := configuredHandler(args)
			valid := size >= 64 && size <= 256 && (suffix == "" || suffix == "\n")
			if (err == nil) != valid {
				t.Fatal("CLI key file validation disagrees with documented format")
			}
		}
	}
}

func TestShutdownWaitsForActiveResponse(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, release := make(chan struct{}), make(chan struct{})
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-release
		_, _ = io.WriteString(w, "complete")
	})}
	defer server.Close()
	done := make(chan error, 1)
	go func() { done <- serve(ctx, server, listener) }()
	response := make(chan string, 1)
	go func() {
		client := &http.Client{Timeout: 5 * time.Second}
		result, err := client.Get("http://" + listener.Addr().String())
		if err != nil {
			response <- "failed"
			return
		}
		defer result.Body.Close()
		body, _ := io.ReadAll(result.Body)
		response <- string(body)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("request did not start")
	}
	cancel()
	select {
	case err := <-done:
		close(release)
		t.Fatalf("exited before draining: %v", err)
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	if got := <-response; got != "complete" {
		t.Fatalf("interrupted response: %q", got)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("server did not stop after draining")
	}
}

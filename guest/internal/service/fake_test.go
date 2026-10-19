//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

type fixture struct {
	t         *testing.T
	dir, path string
	config    fakeConfig
	inventory map[string]any
	network   map[string]any
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	f := &fixture{t: t, dir: t.TempDir()}
	f.path = filepath.Join(f.dir, "guest.json")
	f.config = fakeConfig{
		Schema: "nib.guest-fake/v1", EngineAddress: "127.0.0.1:18000",
		Devices:       []fakeDevice{{"gpu0", "gpu"}, {"gpu1", "gpu"}, {"switch0", "switch"}},
		InventoryFile: "inventory.json", NetworkFile: "network.json", Egress: []string{},
		Weights: []fakeWeight{{Path: "weights.bin", Bytes: 9, SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte("synthetic")))}},
	}
	f.inventory = map[string]any{"schema": "nib.fake-inventory/v1", "devices": []map[string]any{
		{"id": "gpu0", "kind": "gpu", "mode": "fake-cc", "approved": true, "debug": false},
		{"id": "gpu1", "kind": "gpu", "mode": "fake-cc", "approved": true, "debug": false},
		{"id": "switch0", "kind": "switch", "mode": "fake-cc", "approved": true, "debug": false},
	}}
	policy, err := nftables.Render(f.config.Egress)
	if err != nil {
		t.Fatal(err)
	}
	f.network = map[string]any{"schema": "nib.fake-network/v1", "applied": true, "policy_sha256": fmt.Sprintf("%x", sha256.Sum256(policy))}
	f.write("weights.bin", []byte("synthetic"))
	f.save()
	return f
}

func (f *fixture) write(name string, data []byte) {
	f.t.Helper()
	if err := os.WriteFile(filepath.Join(f.dir, name), data, 0600); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixture) json(name string, value any) {
	f.t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		f.t.Fatal(err)
	}
	f.write(name, b)
}

func (f *fixture) save() {
	f.json("guest.json", f.config)
	f.json("inventory.json", f.inventory)
	f.json("network.json", f.network)
}

func (f *fixture) session() *fakeSession {
	f.t.Helper()
	s, err := openFakeConfig(f.path)
	if err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(func() { s.root.Close() })
	return s
}

func TestFakeGatesPositive(t *testing.T) {
	f := newFixture(t)
	// Include every expected GPU and fabric device, not just the first GPU.
	devices := f.inventory["devices"].([]map[string]any)
	for i := 2; i < 8; i++ {
		id := fmt.Sprintf("gpu%d", i)
		f.config.Devices = append(f.config.Devices, fakeDevice{id, "gpu"})
		devices = append(devices, map[string]any{"id": id, "kind": "gpu", "mode": "fake-cc", "approved": true, "debug": false})
	}
	f.inventory["devices"] = devices
	f.save()
	for _, role := range []string{"network", "gpu-gate", "weights"} {
		var out, errs bytes.Buffer
		if code := Run(context.Background(), role, []string{"--config", f.path}, &out, &errs); code != 0 || errs.Len() != 0 || !strings.Contains(out.String(), "NIBFAKE:") || !strings.Contains(out.String(), "no hardware") {
			t.Fatalf("%s: code=%d out=%s err=%s", role, code, &out, &errs)
		}
	}
}

func TestDocumentedFixture(t *testing.T) {
	s, err := openFakeConfig("../../testdata/fake/guest.json")
	if err != nil {
		t.Fatal(err)
	}
	defer s.root.Close()
	if err := s.checkGates(); err != nil {
		t.Fatal(err)
	}
}

func TestFakeInventoryMutations(t *testing.T) {
	for _, kind := range []string{"gpu", "switch"} {
		for _, mutation := range []string{"missing", "extra", "duplicate", "id", "kind", "mode", "approved", "debug", "missing-debug", "null-debug", "unknown"} {
			t.Run(kind+"/"+mutation, func(t *testing.T) {
				f := newFixture(t)
				d := f.inventory["devices"].([]map[string]any)
				i := 1
				if kind == "switch" {
					i = 2
				}
				switch mutation {
				case "missing":
					d = append(d[:i], d[i+1:]...)
				case "extra":
					d = append(d, d[i])
				case "duplicate":
					d[i] = d[0]
				case "id":
					d[i]["id"] = "unexpected"
				case "kind":
					d[i]["kind"] = "other"
				case "mode":
					d[i]["mode"] = "cc-on"
				case "approved":
					d[i]["approved"] = false
				case "debug":
					d[i]["debug"] = true
				case "missing-debug":
					delete(d[i], "debug")
				case "null-debug":
					d[i]["debug"] = nil
				case "unknown":
					d[i]["verified"] = true
				}
				f.inventory["devices"] = d
				f.save()
				if nibfakeGPU(f.session()) == nil {
					t.Fatal("accepted inventory mutation")
				}
			})
		}
	}
}

func TestFakeWeightsAndNetworkMutations(t *testing.T) {
	for _, mutation := range []string{"corruption", "truncated", "oversized", "missing", "symlink", "directory", "policy", "not-applied", "network-schema", "inventory-schema"} {
		t.Run(mutation, func(t *testing.T) {
			f := newFixture(t)
			s := f.session()
			switch mutation {
			case "corruption":
				f.write("weights.bin", []byte("corrupted"))
			case "truncated":
				f.write("weights.bin", []byte("short"))
			case "oversized":
				f.write("weights.bin", []byte("synthetic-extra"))
			case "missing", "symlink", "directory":
				if err := os.Remove(filepath.Join(f.dir, "weights.bin")); err != nil {
					t.Fatal(err)
				}
				if mutation == "symlink" {
					outside := filepath.Join(t.TempDir(), "outside")
					if err := os.WriteFile(outside, []byte("synthetic"), 0600); err != nil {
						t.Fatal(err)
					}
					if err := os.Symlink(outside, filepath.Join(f.dir, "weights.bin")); err != nil {
						t.Fatal(err)
					}
				}
				if mutation == "directory" {
					if err := os.Mkdir(filepath.Join(f.dir, "weights.bin"), 0700); err != nil {
						t.Fatal(err)
					}
				}
			case "policy":
				f.network["policy_sha256"] = strings.Repeat("0", 64)
				f.save()
			case "not-applied":
				f.network["applied"] = false
				f.save()
			case "network-schema":
				f.network["schema"] = "real-network"
				f.save()
			case "inventory-schema":
				f.inventory["schema"] = "real-inventory"
				f.save()
			}
			if s.checkGates() == nil {
				t.Fatal("accepted broken gate")
			}
			var out, errs bytes.Buffer
			if code := Run(context.Background(), "engine", []string{"--config", f.path}, &out, &errs); code != 1 || out.Len() != 0 {
				t.Fatal("engine started with a broken gate")
			}
		})
	}
}

func TestFakeConfigRejections(t *testing.T) {
	for _, mutation := range []string{"schema", "wildcard", "ipv6", "hostname", "privileged", "frontdoor", "devices", "duplicate-device", "kind", "switch-only", "nine-gpus", "many-devices", "inventory-path", "network-alias", "weight-path", "duplicate-weight", "weight-alias", "empty-weights", "empty-file", "big-file", "many-files", "total", "hash", "uppercase-hash", "egress-null", "egress-hostname"} {
		t.Run(mutation, func(t *testing.T) {
			f := newFixture(t)
			c := &f.config
			switch mutation {
			case "schema":
				c.Schema = "nib.guest/v1"
			case "wildcard":
				c.EngineAddress = "0.0.0.0:8000"
			case "ipv6":
				c.EngineAddress = "[::1]:8000"
			case "hostname":
				c.EngineAddress = "localhost:8000"
			case "privileged":
				c.EngineAddress = "127.0.0.1:443"
			case "frontdoor":
				c.EngineAddress = "127.0.0.1:8443"
			case "devices":
				c.Devices = nil
			case "duplicate-device":
				c.Devices[1] = c.Devices[0]
			case "kind":
				c.Devices[0].Kind = "other"
			case "switch-only":
				c.Devices = []fakeDevice{{"switch0", "switch"}}
			case "nine-gpus":
				c.Devices = nil
				for i := 0; i < 9; i++ {
					c.Devices = append(c.Devices, fakeDevice{fmt.Sprint(i), "gpu"})
				}
			case "many-devices":
				c.Devices = make([]fakeDevice, 17)
			case "inventory-path":
				c.InventoryFile = "../inventory.json"
			case "network-alias":
				c.NetworkFile = c.InventoryFile
			case "weight-path":
				c.Weights[0].Path = "/tmp/weights"
			case "duplicate-weight":
				c.Weights = append(c.Weights, c.Weights[0])
			case "weight-alias":
				c.Weights[0].Path = c.NetworkFile
			case "empty-weights":
				c.Weights = nil
			case "empty-file":
				c.Weights[0].Bytes = 0
			case "big-file":
				c.Weights[0].Bytes = fileLimit + 1
			case "many-files":
				c.Weights = make([]fakeWeight, 33)
			case "total":
				w := c.Weights[0]
				c.Weights = nil
				for i := 0; i < 5; i++ {
					w.Path = fmt.Sprint(i)
					w.Bytes = fileLimit
					c.Weights = append(c.Weights, w)
				}
			case "hash":
				c.Weights[0].SHA256 = "bad"
			case "uppercase-hash":
				c.Weights[0].SHA256 = strings.ToUpper(c.Weights[0].SHA256)
			case "egress-null":
				c.Egress = nil
			case "egress-hostname":
				c.Egress = []string{"example.com"}
			}
			f.save()
			if s, err := openFakeConfig(f.path); err == nil {
				s.root.Close()
				t.Fatal("accepted invalid configuration")
			}
		})
	}
}

func TestStrictFakeJSON(t *testing.T) {
	for _, input := range []string{
		`{"model":"a","model":"b"}`, `{"model":"a","Model":"b"}`, `{"Model":"a"}`,
		`{"model":null}`, `null`, `{"unknown":true}`, `{"model":"a"} {}`, `{"model":1}`, `{"model":`,
		strings.Repeat("[", 18) + strings.Repeat("]", 18),
	} {
		var v struct {
			Model string `json:"model"`
		}
		if strictJSON([]byte(input), &v) == nil {
			t.Fatalf("accepted %q", input)
		}
	}
	for _, input := range []string{`{"\u017fchema":"nib.guest-fake/v1"}`, `{"schema":"wrong","\u017fchema":"nib.guest-fake/v1"}`} {
		var v struct {
			Schema string `json:"schema"`
		}
		if strictJSON([]byte(input), &v) == nil {
			t.Fatal("accepted Unicode case alias")
		}
	}
	f := newFixture(t)
	f.write("guest.json", bytes.Repeat([]byte(" "), configLimit+1))
	if s, err := openFakeConfig(f.path); err == nil {
		s.root.Close()
		t.Fatal("accepted oversized config")
	}
}

const chatBody = `{"model":"nibfake-model","messages":[{"role":"user","content":"SECRET_PROMPT"}]}`

func TestFakeHTTPAndPrivacy(t *testing.T) {
	f := newFixture(t)
	h := f.session().fakeHandler(func() { t.Error("unexpected gate failure") })
	for _, tc := range []struct {
		method, path, body string
		status             int
		contains           string
	}{
		{"GET", "/health", "", 200, `"hardware_validated":false`},
		{"GET", "/v1/models", "", 200, `"id":"nibfake-model"`},
		{"POST", "/v1/chat/completions", chatBody, 200, `"total_tokens":2`},
		{"POST", "/v1/chat/completions", strings.Replace(chatBody, `"model":`, `"stream":true,"stream_options":{"include_usage":true},"model":`, 1), 200, "data: [DONE]"},
		{"GET", "/SECRET_PATH", "", 404, ""},
		{"GET", "/health?SECRET_QUERY", "", 400, ""},
		{"POST", "/health", "SECRET_BODY", 404, ""},
		{"POST", "/v1/chat/completions", `{"model":"SECRET_MODEL"}`, 400, ""},
		{"POST", "/v1/chat/completions", strings.Replace(chatBody, `"user"`, `"invalid"`, 1), 400, ""},
		{"POST", "/v1/chat/completions", strings.Repeat("SECRET", 12000), 413, ""},
		{"POST", "/v1/chat/completions", strings.Replace(chatBody, `"model":`, `"model":"nibfake-model","model":`, 1), 400, ""},
	} {
		r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
		r.Header.Set("X-Private", "SECRET_HEADER")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.contains) {
			t.Fatalf("%s %s: %d %s", tc.method, tc.path, w.Code, w.Body)
		}
		if w.Header().Get("X-Nib-Simulation") != "nibfake" || strings.Contains(w.Body.String()+fmt.Sprint(w.Header()), "SECRET") {
			t.Fatal("missing simulation marker or input reflected")
		}
		if strings.Contains(tc.body, `"stream":true`) {
			if !w.Flushed || w.Header().Get("Content-Type") != "text/event-stream" || !strings.Contains(w.Body.String(), `"total_tokens":2`) {
				t.Fatal("stream missing flush or usage")
			}
		}
	}
}

type blockedBody struct {
	entered chan struct{}
	release chan struct{}
	once    sync.Once
	reader  io.Reader
}

func (b *blockedBody) Read(p []byte) (int, error) {
	b.once.Do(func() { close(b.entered); <-b.release })
	return b.reader.Read(p)
}
func (b *blockedBody) Close() error { return nil }

func TestFakeInFlightGateLossAndAdmission(t *testing.T) {
	f := newFixture(t)
	h := f.session().fakeHandler(func() {})
	release := make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	defer unblock()
	results := make(chan *httptest.ResponseRecorder, 8)
	for i := 0; i < 8; i++ {
		b := &blockedBody{entered: make(chan struct{}), release: release, reader: strings.NewReader(chatBody)}
		r := httptest.NewRequest("POST", "/v1/chat/completions", b)
		go func() { w := httptest.NewRecorder(); h.ServeHTTP(w, r); results <- w }()
		select {
		case <-b.entered:
		case <-time.After(3 * time.Second):
			t.Fatal("handler did not enter")
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/health", nil))
	if w.Code != 429 || w.Header().Get("Retry-After") != "1" {
		t.Fatal("admission did not reject excess request")
	}
	f.write("weights.bin", []byte("corrupted"))
	unblock()
	for i := 0; i < 8; i++ {
		select {
		case w := <-results:
			if w.Code != 503 || strings.Contains(w.Body.String(), "PONG") || w.Header().Get("Retry-After") != "1" {
				t.Fatal("in-flight request served through failed gate")
			}
		case <-time.After(3 * time.Second):
			t.Fatal("handler did not stop")
		}
	}
	f.write("weights.bin", []byte("synthetic"))
	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/health", nil))
	if w.Code != 503 {
		t.Fatal("failed handler recovered without restart")
	}
}

type readyWriter struct {
	ready chan struct{}
	bytes.Buffer
}

func (w *readyWriter) Write(p []byte) (int, error) {
	n, err := w.Buffer.Write(p)
	select {
	case <-w.ready:
	default:
		close(w.ready)
	}
	return n, err
}

func TestFakeEngineLifecycle(t *testing.T) {
	for _, mutation := range []string{"cancel", "weights", "gpu", "network"} {
		t.Run(mutation, func(t *testing.T) {
			f := newFixture(t)
			l, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			f.config.EngineAddress = l.Addr().String()
			l.Close()
			f.save()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			out := &readyWriter{ready: make(chan struct{})}
			var errs bytes.Buffer
			done := make(chan int, 1)
			go func() { done <- Run(ctx, "engine", []string{"--config", f.path}, out, &errs) }()
			select {
			case <-out.ready:
			case code := <-done:
				t.Fatalf("startup failed: %d %s", code, &errs)
			case <-time.After(5 * time.Second):
				t.Fatal("startup timeout")
			}
			client := &http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{Proxy: nil, DisableKeepAlives: true}}
			resp, err := client.Get("http://" + f.config.EngineAddress + "/health")
			if err != nil {
				t.Fatal(err)
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode != 200 {
				t.Fatal("engine not healthy")
			}
			want := 1
			switch mutation {
			case "cancel":
				cancel()
				want = 0
			case "weights":
				f.write("weights.bin", []byte("corrupted"))
			case "gpu":
				f.inventory["devices"] = []any{}
				f.save()
			case "network":
				f.network["applied"] = false
				f.save()
			}
			select {
			case code := <-done:
				if code != want {
					t.Fatalf("exit %d, want %d", code, want)
				}
			case <-time.After(5 * time.Second):
				t.Fatal("engine did not stop")
			}
			if strings.Contains(out.String()+errs.String(), "SECRET") {
				t.Fatal("private input logged")
			}
			if conn, err := net.DialTimeout("tcp4", f.config.EngineAddress, time.Second); err == nil {
				conn.Close()
				t.Fatal("listener survived shutdown")
			}
		})
	}
}

func TestFakeCancelledAndOccupied(t *testing.T) {
	f := newFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if Run(ctx, "engine", []string{"--config", f.path}, io.Discard, io.Discard) != 1 {
		t.Fatal("cancelled startup passed")
	}
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	f.config.EngineAddress = l.Addr().String()
	f.save()
	if Run(context.Background(), "engine", []string{"--config", f.path}, io.Discard, io.Discard) != 1 {
		t.Fatal("occupied listener passed")
	}
}

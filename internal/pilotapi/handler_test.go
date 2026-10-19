// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

const testKey = "local-pilot-test-key-not-a-real-credential"
const requestBody = `{"model":"qwen/qwen3-0.6b","messages":[{"role":"user","content":"PONG"}]}`

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestCatalogExactPricesAndSchema(t *testing.T) {
	data, err := Catalog("0.10", "0.20")
	if err != nil || catalog.Validate(data) != nil {
		t.Fatalf("invalid catalog: %v", err)
	}
	for _, want := range []string{`"input":"0.10"`, `"output":"0.20"`, `"context_length":4096`, `"max_output_tokens":256`} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("missing %s", want)
		}
	}
	for _, bad := range []string{"", "NaN", "-1", "1e2", "01", "0.1\n", "1000000"} {
		if _, err := Catalog(bad, "0.10"); err == nil {
			t.Fatal("invalid input price accepted")
		}
		if _, err := Catalog("0.10", bad); err == nil {
			t.Fatal("invalid output price accepted")
		}
	}
}

func TestRequestLimits(t *testing.T) {
	for _, suffix := range []string{``, `,"max_tokens":16`, `,"max_completion_tokens":32`, `,"max_tokens":16,"max_completion_tokens":32`, `,"stream":true,"stream_options":{"include_usage":true}`} {
		body := strings.TrimSuffix(requestBody, "}") + suffix + "}"
		bounded, err := BoundRequest([]byte(body))
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]json.RawMessage
		_ = json.Unmarshal(bounded, &value)
		if _, ok := value["max_completion_tokens"]; ok {
			t.Fatal("alias not normalized")
		}
		var limit int
		_ = json.Unmarshal(value["max_tokens"], &limit)
		if limit < 1 || limit > MaxOutput {
			t.Fatal("output not bounded")
		}
	}
	for _, field := range []string{`"max_tokens":0`, `"max_tokens":-1`, `"max_tokens":257`, `"max_tokens":null`, `"max_tokens":1.1`,
		`"max_completion_tokens":9999999`, `"max_completion_tokens":"8"`, `"n":2`, `"tools":[]`, `"n_predict":-1`, `"response_format":{}`} {
		if _, err := BoundRequest([]byte(strings.TrimSuffix(requestBody, "}") + "," + field + "}")); err == nil {
			t.Fatalf("accepted %s", field)
		}
	}
	for _, body := range []string{"null", "[]", "{}", requestBody + "{}", strings.Replace(requestBody, Model, "unknown", 1),
		strings.Replace(requestBody, `"content":"PONG"`, `"content":[{"type":"image_url"}]`, 1)} {
		if _, err := BoundRequest([]byte(body)); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
}

func TestAuthenticationPathsAndStreamingUsage(t *testing.T) {
	h, err := New(testKey, "0.1", "0.1")
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != "http://127.0.0.1:8000/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+testKey || r.Header.Get("X-Forwarded-For") != "" {
			t.Fatal("upstream boundary changed")
		}
		deadline, ok := r.Context().Deadline()
		if !ok || time.Until(deadline) > 120*time.Second {
			t.Fatal("inference deadline missing")
		}
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), `"max_tokens":256`) {
			t.Fatal("default output limit missing")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream"}},
			Body: io.NopCloser(strings.NewReader("data: {\"usage\":{\"prompt_tokens\":11,\"completion_tokens\":3,\"total_tokens\":14}}\n\ndata: [DONE]\n\n"))}, nil
	})
	for _, tc := range []struct {
		method, path, key, body string
		status                  int
	}{
		{"GET", "/v1/models", "", "", 401},
		{"POST", "/v1/chat/completions", "bad", requestBody, 401},
		{"GET", "/v1/models", testKey, "", 200},
		{"POST", "/v1/chat/completions", testKey, requestBody, 200},
		{"POST", "/v1/chat/completions?url=private", testKey, requestBody, 400},
		{"POST", "/v1/completions", testKey, requestBody, 404},
		{"POST", "/v1/chat/completions", testKey, strings.Repeat("x", 65537), 413},
	} {
		r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
		if tc.key != "" {
			r.Header.Set("Authorization", "Bearer "+tc.key)
		}
		r.Header.Set("X-Forwarded-For", "do-not-forward")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Fatalf("%s %s: got %d want %d", tc.method, tc.path, w.Code, tc.status)
		}
		if tc.method == "POST" && tc.status == 200 && !strings.Contains(w.Body.String(), `"total_tokens":14`) {
			t.Fatal("stream usage changed")
		}
	}
	if calls != 1 {
		t.Fatalf("invalid requests reached engine: %d calls", calls)
	}
}

func TestOverloadAndDuplicateAuthentication(t *testing.T) {
	h, _ := New(testKey, "0.1", "0.1")
	h.slots <- struct{}{}
	h.slots <- struct{}{}
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody))
	r.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 429 || w.Header().Get("Retry-After") != "1" {
		t.Fatal("overload must fail before inference")
	}
	r = httptest.NewRequest("GET", "/v1/models", nil)
	r.Header.Add("Authorization", "Bearer "+testKey)
	r.Header.Add("Authorization", "Bearer "+testKey)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatal("duplicate authentication accepted")
	}
}

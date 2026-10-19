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
	data, err := Catalog("0.10", "0.20", time.Time{})
	if err != nil || catalog.Validate(data) != nil {
		t.Fatalf("invalid catalog: %v", err)
	}
	for _, want := range []string{`"input":"0.10"`, `"output":"0.20"`, `"context_length":4096`, `"max_output_tokens":256`} {
		if !strings.Contains(string(data), want) {
			t.Fatalf("missing %s", want)
		}
	}
	for _, bad := range []string{"", "NaN", "-1", "1e2", "01", "0.1\n", "1000000"} {
		if _, err := Catalog(bad, "0.10", time.Time{}); err == nil {
			t.Fatal("invalid input price accepted")
		}
		if _, err := Catalog("0.10", bad, time.Time{}); err == nil {
			t.Fatal("invalid output price accepted")
		}
	}
}

func TestRetirementValidationAndCatalog(t *testing.T) {
	now := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	for _, value := range []string{"bad", "2026-10-19T12:00:00Z", "2026-10-19T11:59:59Z",
		"2026-10-20T12:00:01Z", "2026-10-19T13:00:00+00:00", "2026-10-19T13:00:00.1Z"} {
		if _, err := parseRetirement(value, now); err == nil {
			t.Fatalf("accepted invalid cutoff %q", value)
		}
	}
	cutoff, err := parseRetirement("2026-10-20T12:00:00Z", now)
	if err != nil || cutoff.Sub(now) != 24*time.Hour {
		t.Fatal("exact 24h cutoff rejected")
	}
	data, err := Catalog("0.10", "0.20", cutoff)
	if err != nil || catalog.Validate(data) != nil || !strings.Contains(string(data), `"retirement_at":"2026-10-20T12:00:00Z"`) {
		t.Fatalf("retirement missing from valid catalog: %s", data)
	}
	if _, err := New(testKey, "0.1", "0.1", "not-a-time"); err == nil {
		t.Fatal("invalid operator cutoff accepted")
	}
}

type onRead struct {
	io.Reader
	once func()
}

func (r *onRead) Read(p []byte) (int, error) {
	if r.once != nil {
		r.once()
		r.once = nil
	}
	return r.Reader.Read(p)
}

func TestRetirementRejectsAdmissionAndSlowUpload(t *testing.T) {
	cutoff := time.Now().Add(time.Hour).UTC().Truncate(time.Second)
	h, err := New(testKey, "0.1", "0.1", cutoff.Format(time.RFC3339))
	if err != nil {
		t.Fatal(err)
	}
	h.proxy.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("retired request reached inference")
		return nil, nil
	})
	for _, delta := range []time.Duration{0, time.Second} {
		h.now = func() time.Time { return cutoff.Add(delta) }
		for _, path := range []string{"/health", "/v1/models", "/v1/chat/completions"} {
			method := "GET"
			if path == "/v1/chat/completions" {
				method = "POST"
			}
			r := httptest.NewRequest(method, path, strings.NewReader(requestBody))
			r.Header.Set("Authorization", "Bearer "+testKey)
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if w.Code != 503 || !strings.Contains(w.Body.String(), "pilot_retired") {
				t.Fatalf("%s admitted at/after cutoff", path)
			}
		}
	}
	h.now = func() time.Time { return cutoff.Add(-time.Second) }
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/v1/models", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), cutoff.Format(time.RFC3339)) {
		t.Fatal("pre-cutoff catalog did not disclose expiry")
	}
	r := httptest.NewRequest("POST", "/v1/chat/completions", &onRead{Reader: strings.NewReader(requestBody),
		once: func() { h.now = func() time.Time { return cutoff } }})
	r.Header.Set("Authorization", "Bearer "+testKey)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 503 || len(h.slots) != 0 {
		t.Fatal("slow upload crossed retirement or leaked capacity")
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

func TestRequestOptionsCannotEnableNativeFeatures(t *testing.T) {
	for _, field := range []string{
		`"n_probs":1000000`, `"logprobs":true`, `"top_logprobs":1000000`,
		`"grammar":"root ::= .+"`, `"json_schema":{}`, `"chat_template_kwargs":{"enable_thinking":true}`,
		`"reasoning_budget":-1`, `"reasoning":{"effort":"low","budget_tokens":999999}`,
		`"thinking":{"type":"enabled"}`, `"reasoning_effort":"max"`,
		`"stream_options":{"include_usage":true,"extra":1}`, `"stream_options":null`,
		`"stream":"yes"`, `"stream":null`, `"temperature":3`, `"temperature":null`,
		`"top_p":1.1`, `"top_k":1000000`, `"seed":-1`, `"stop":[null]`, `"stop":[]`,
		`"presence_penalty":-3`, `"mirostat":2`, `"cache_prompt":true`,
	} {
		if _, err := BoundRequest([]byte(strings.TrimSuffix(requestBody, "}") + "," + field + "}")); err == nil {
			t.Fatalf("native/unbounded option accepted: %s", field)
		}
	}
	for _, message := range []string{`"content":null`, `"content":"PONG","tool_calls":[]`, `"content":"PONG","name":"override"`} {
		if _, err := BoundRequest([]byte(strings.Replace(requestBody, `"content":"PONG"`, message, 1))); err == nil {
			t.Fatalf("invalid message accepted: %s", message)
		}
	}
	for _, field := range []string{`"temperature":0`, `"top_p":1`, `"top_k":40`, `"seed":42`, `"stop":["END"]`,
		`"presence_penalty":-2`, `"frequency_penalty":2`, `"thinking":{"type":"disabled"}`, `"reasoning":{"effort":"low"}`, `"reasoning_effort":"low"`} {
		bounded, err := BoundRequest([]byte(strings.TrimSuffix(requestBody, "}") + "," + field + "}"))
		if err != nil {
			t.Fatalf("bounded option rejected: %s", field)
		}
		if strings.Contains(string(bounded), "thinking") || strings.Contains(string(bounded), "reasoning") {
			t.Fatal("compatibility hint reached the native engine")
		}
	}
}

func TestAuthenticationPathsAndStreamingUsage(t *testing.T) {
	h, err := New(testKey, "0.1", "0.1", "")
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
		{"GET", "/v1/models", "", "", 200},
		{"POST", "/v1/chat/completions", "", requestBody, 401},
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
	h, _ := New(testKey, "0.1", "0.1", "")
	h.slots <- struct{}{}
	h.slots <- struct{}{}
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody))
	r.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 429 || w.Header().Get("Retry-After") != "1" {
		t.Fatal("overload must fail before inference")
	}
	r = httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody))
	r.Header.Add("Authorization", "Bearer "+testKey)
	r.Header.Add("Authorization", "Bearer "+testKey)
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatal("duplicate authentication accepted")
	}
}

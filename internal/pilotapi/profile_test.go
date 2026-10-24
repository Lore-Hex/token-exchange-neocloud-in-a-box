// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
	"go.yaml.in/yaml/v3"
)

const selectedModel = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"

func selectedRequest(fields string) string {
	return `{"model":"` + selectedModel + `","messages":[{"role":"user","content":"PONG"}]` + fields + `}`
}

func selectedHandler(t *testing.T) *Handler {
	t.Helper()
	h, err := NewWithProfile(ProfileSelectedDeepSeek, testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestProfilesClosedAndQwenUnchanged(t *testing.T) {
	for _, name := range []string{"", "unknown", "Qwen", "selected-deepseek ", Model, selectedModel, "http://example.invalid"} {
		if _, err := NewWithProfile(name, testProvider(), testKey, "0.10", "0.20", ""); err == nil || err.Error() != "unsupported API profile" {
			t.Fatalf("invalid profile accepted or echoed: %q", name)
		}
	}
	legacy, err := New(testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	explicit, err := NewWithProfile(ProfileQwen, testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range []*Handler{legacy, explicit} {
		if string(h.catalog) != qwenCatalog || cap(h.slots) != 2 || h.profile != qwenProfile() {
			t.Fatal("Qwen defaults changed")
		}
	}
	for _, tc := range []struct {
		provider                       ProviderConfig
		key, input, output, retirement string
	}{
		{ProviderConfig{}, testKey, "0.10", "0.20", ""},
		{testProvider(), "short", "0.10", "0.20", ""},
		{testProvider(), testKey, "", "0.20", ""},
		{testProvider(), testKey, "0.10", "NaN", ""},
		{testProvider(), testKey, "0.10", "0.20", "2000-01-01T00:00:00Z"},
	} {
		if _, err := NewWithProfile(ProfileSelectedDeepSeek, tc.provider, tc.key, tc.input, tc.output, tc.retirement); err == nil {
			t.Fatal("selected profile bypassed existing startup validation")
		}
	}
}

func TestSelectedProfileCatalogAndPackIdentity(t *testing.T) {
	data, err := os.ReadFile("../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/pack.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var pack struct {
		CatalogID string `yaml:"tr_model_id"`
		LabName   string `yaml:"served_model_name"`
	}
	if err := yaml.Unmarshal(data, &pack); err != nil {
		t.Fatal(err)
	}
	h := selectedHandler(t)
	if pack.CatalogID != selectedModel || h.profile.model != pack.CatalogID || pack.LabName == pack.CatalogID || pack.LabName == "" {
		t.Fatal("selected identity drifted or lab/canonical names were conflated")
	}
	if _, err := h.profile.boundRequest([]byte(strings.ReplaceAll(selectedRequest(""), selectedModel, pack.LabName))); err == nil {
		t.Fatal("bare lab alias silently accepted")
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/v1/models", nil))
	if w.Code != 200 || catalog.Validate(w.Body.Bytes()) != nil {
		t.Fatal("selected catalog fails the pinned v2 schema")
	}
	var doc struct {
		Data []struct {
			ID           string          `json:"id"`
			Owner        string          `json:"owned_by"`
			Context      int             `json:"context_length"`
			Output       int             `json:"max_output_tokens"`
			Capabilities map[string]bool `json:"capabilities"`
		}
	}
	if err := json.Unmarshal(w.Body.Bytes(), &doc); err != nil || len(doc.Data) != 1 {
		t.Fatal("selected catalog must contain exactly one model")
	}
	m := doc.Data[0]
	if m.ID != selectedModel || m.Owner != "dealignai" || m.Context != 8192 || m.Output != 256 || cap(h.slots) != 1 {
		t.Fatal("selected catalog/admission limits changed")
	}
	if !reflect.DeepEqual(m.Capabilities, map[string]bool{"streaming": true, "tools": false, "reasoning": false, "structured_output": false, "prompt_caching": false}) {
		t.Fatal("selected profile invented capabilities")
	}
	for _, claim := range []string{"privacy", "confidential", "zdr", "e2ee", "attestation"} {
		if strings.Contains(w.Body.String(), claim) {
			t.Fatalf("invented privacy claim: %s", claim)
		}
	}
}

func TestSelectedProfileRequestBoundary(t *testing.T) {
	h := selectedHandler(t)
	for _, tc := range []struct {
		fields string
		limit  int
	}{
		{"", 256}, {`,"max_tokens":1`, 1}, {`,"max_completion_tokens":256`, 256},
		{`,"max_tokens":16,"max_completion_tokens":8`, 8},
		{`,"stream":true,"stream_options":{"include_usage":true}`, 256},
	} {
		bounded, err := h.profile.boundRequest([]byte(selectedRequest(tc.fields)))
		if err != nil {
			t.Fatal(err)
		}
		var got map[string]any
		if err := json.Unmarshal(bounded, &got); err != nil {
			t.Fatal(err)
		}
		if got["model"] != selectedModel || got["max_tokens"] != float64(tc.limit) || got["max_completion_tokens"] != nil {
			t.Fatal("selected model or output bound changed")
		}
	}
	for _, model := range []string{Model, "deepseek-v4.1-flash-1017-uncensored-fp8", "deepseek/deepseek-v4.1-flash-1017", strings.ToUpper(selectedModel), selectedModel + "@1"} {
		if _, err := h.profile.boundRequest([]byte(strings.ReplaceAll(selectedRequest(""), selectedModel, model))); err == nil {
			t.Fatalf("accepted alternate model: %q", model)
		}
	}
	for _, field := range []string{
		`"max_tokens":0`, `"max_tokens":257`, `"max_tokens":null`, `"max_tokens":1.5`,
		`"max_tokens":1,"max_completion_tokens":257`, `"n":2`, `"tools":[]`, `"tool_choice":"auto"`,
		`"response_format":{}`, `"thinking":{"type":"disabled"}`, `"thinking":{"type":"enabled"}`,
		`"reasoning":{"effort":"none"}`, `"reasoning_effort":"none"`, `"reasoning_effort":"high"`,
		`"chat_template_kwargs":{"enable_thinking":false}`, `"cache_prompt":true`, `"stream":null`,
		`"stream_options":{"include_usage":true,"extra":1}`, `"n_predict":-1`,
	} {
		if _, err := h.profile.boundRequest([]byte(selectedRequest("," + field))); err == nil {
			t.Fatalf("unsupported selected option accepted: %s", field)
		}
	}
	for _, message := range []string{`"role":"tool","content":"PONG"`, `"role":"user","content":[{"type":"image_url"}]`, `"role":"assistant","content":"PONG","tool_calls":[]`} {
		if _, err := h.profile.boundRequest([]byte(strings.Replace(selectedRequest(""), `"role":"user","content":"PONG"`, message, 1))); err == nil {
			t.Fatal("non-text/tool message accepted")
		}
	}
}

func TestSelectedProfileProxyPassThrough(t *testing.T) {
	for _, tc := range []struct{ contentType, body string }{
		{"application/json", `{"model":"` + selectedModel + `","choices":[],"usage":{"prompt_tokens":17,"completion_tokens":9,"total_tokens":26}}`},
		{"text/event-stream", "data: {\"model\":\"" + selectedModel + "\",\"choices\":[]}\n\ndata: {\"usage\":{\"prompt_tokens\":17,\"completion_tokens\":9,\"total_tokens\":26}}\n\ndata: [DONE]\n\n"},
		{"application/json", `{"choices":[]}`}, // Absent usage stays absent.
	} {
		h := selectedHandler(t)
		h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			if r.URL.String() != "http://127.0.0.1:8000/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+testKey || r.Header.Get("X-Forwarded-For") != "" || r.Header.Get("Cookie") != "" {
				t.Fatal("selected profile changed fixed upstream/credential boundary")
			}
			deadline, ok := r.Context().Deadline()
			if !ok || time.Until(deadline) > 120*time.Second {
				t.Fatal("inference deadline missing")
			}
			body, err := io.ReadAll(r.Body)
			if err != nil || !strings.Contains(string(body), `"model":"`+selectedModel+`"`) || !strings.Contains(string(body), `"max_tokens":256`) {
				t.Fatal("request model rewritten or limit missing")
			}
			return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {tc.contentType}}, Body: io.NopCloser(strings.NewReader(tc.body))}, nil
		})
		r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(selectedRequest(`,"stream":true,"stream_options":{"include_usage":true}`)))
		r.Header.Set("Authorization", "Bearer "+testKey)
		r.Header.Set("X-Forwarded-For", "synthetic-do-not-forward")
		r.Header.Set("Cookie", "synthetic-do-not-forward")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 || w.Body.String() != tc.body || w.Header().Get("Content-Type") != tc.contentType || len(h.slots) != 0 {
			t.Fatal("response/usage changed or admission leaked")
		}
	}
}

func TestSelectedProfileCancellationAndSingleAdmission(t *testing.T) {
	h := selectedHandler(t)
	started, done := make(chan struct{}), make(chan struct{})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		close(started)
		<-r.Context().Done()
		return nil, r.Context().Err()
	})
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(selectedRequest(""))).WithContext(ctx)
	r.Header.Set("Authorization", "Bearer "+testKey)
	go func() {
		defer close(done)
		h.ServeHTTP(httptest.NewRecorder(), r)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("request did not enter transport")
	}
	next := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(selectedRequest("")))
	next.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, next)
	if w.Code != 429 || w.Header().Get("Retry-After") != "1" {
		t.Fatal("second selected request was admitted")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("caller cancellation did not reach upstream")
	}
	if len(h.slots) != 0 {
		t.Fatal("cancellation leaked admission")
	}
}

func TestSelectedProfileRejectionsFailureAndRetirement(t *testing.T) {
	h := selectedHandler(t)
	calls := 0
	h.proxy.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls++
		return nil, errors.New("synthetic-private-upstream-detail")
	})
	for _, tc := range []struct {
		path, body string
		auth       []string
		status     int
	}{
		{"/v1/chat/completions", selectedRequest(""), nil, 401},
		{"/v1/chat/completions", selectedRequest(""), []string{"wrong"}, 401},
		{"/v1/chat/completions", selectedRequest(""), []string{testKey, testKey}, 401},
		{"/v1/chat/completions?synthetic=private", selectedRequest(""), []string{testKey}, 400},
		{"/v1/completions", selectedRequest(""), []string{testKey}, 404},
		{"/v1/chat/completions", strings.Repeat("x", 65537), []string{testKey}, 413},
		{"/v1/chat/completions", requestBody, []string{testKey}, 400},
		{"/v1/chat/completions", selectedRequest(""), []string{testKey}, 502},
	} {
		r := httptest.NewRequest("POST", tc.path, strings.NewReader(tc.body))
		for _, key := range tc.auth {
			r.Header.Add("Authorization", "Bearer "+key)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.status || strings.Contains(w.Body.String(), "private") || len(h.slots) != 0 {
			t.Fatalf("rejection leaked details/capacity or wrong status: %d", w.Code)
		}
	}
	if calls != 1 {
		t.Fatal("rejected request reached upstream")
	}
	cutoff := time.Now().Add(time.Hour).UTC().Truncate(time.Second)
	h, err := NewWithProfile(ProfileSelectedDeepSeek, testProvider(), testKey, "0.10", "0.20", cutoff.Format(time.RFC3339))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(h.catalog), `"retirement_at":"`+cutoff.Format(time.RFC3339)+`"`) {
		t.Fatal("selected retirement missing from catalog")
	}
	h.now = func() time.Time { return cutoff.Add(-time.Second) }
	h.proxy.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("slow upload reached retired inference")
		return nil, nil
	})
	r := httptest.NewRequest("POST", "/v1/chat/completions", &onRead{Reader: strings.NewReader(selectedRequest("")), once: func() { h.now = func() time.Time { return cutoff } }})
	r.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 503 || len(h.slots) != 0 {
		t.Fatal("selected upload crossed retirement or leaked admission")
	}
}

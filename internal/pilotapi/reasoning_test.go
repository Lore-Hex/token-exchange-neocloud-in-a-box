// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSelectedReasoningNormalization(t *testing.T) {
	h := selectedHandler(t)
	for _, effort := range []string{"none", "low", "high", "xhigh", "max"} {
		fields := []string{`"reasoning_effort":"` + effort + `"`, `"reasoning":{"effort":"` + effort + `"}`}
		if effort == "none" {
			fields = append(fields, `"thinking":{"type":"disabled"}`)
		}
		for _, field := range fields {
			t.Run(field, func(t *testing.T) {
				body, err := h.profile.boundRequest([]byte(selectedRequest("," + field)))
				if err != nil {
					t.Fatal(err)
				}
				var got map[string]any
				if err := json.Unmarshal(body, &got); err != nil {
					t.Fatal(err)
				}
				if got["reasoning_effort"] != effort || got["thinking"] != nil || got["reasoning"] != nil || got["chat_template_kwargs"] != nil {
					t.Fatalf("reasoning was lost or gained an override: %s", body)
				}
				if got["max_tokens"] != float64(256) || got["model"] != selectedModel {
					t.Fatal("model or output budget changed")
				}
			})
		}
	}
	body, err := h.profile.boundRequest([]byte(selectedRequest("")))
	if err != nil || strings.Contains(string(body), "reasoning") || strings.Contains(string(body), "thinking") {
		t.Fatal("absent reasoning must preserve the engine default")
	}
}

func TestSelectedReasoningRejectsAmbiguousAndNativeOverrides(t *testing.T) {
	h := selectedHandler(t)
	for _, field := range []string{
		`"reasoning_effort":null`, `"reasoning_effort":50`, `"reasoning_effort":true`,
		`"reasoning_effort":""`, `"reasoning_effort":"LOW"`, `"reasoning_effort":"medium"`,
		`"reasoning_effort":"minimal"`, `"reasoning_effort":"ultra"`,
		`"thinking":false`, `"thinking":null`, `"thinking":{"type":"enabled"}`,
		`"thinking":{"type":"disabled","budget_tokens":8}`, `"thinking":{"type":null}`,
		`"reasoning":null`, `"reasoning":{}`, `"reasoning":{"effort":null}`,
		`"reasoning":{"effort":"low","budget_tokens":8}`, `"reasoning":{"enabled":true}`,
		`"reasoning_effort":"none","thinking":{"type":"disabled"}`,
		`"reasoning_effort":"high","thinking":{"type":"disabled"}`,
		`"reasoning_effort":"low","reasoning":{"effort":"low"}`,
		`"thinking":{"type":"disabled"},"reasoning":{"effort":"high"}`,
		`"reasoning_effort":"low","chat_template_kwargs":{"enable_thinking":false}`,
		`"reasoning_effort":"low","thinking_token_budget":8`,
		`"reasoning_effort":"max","max_tokens":257`,
	} {
		t.Run(field, func(t *testing.T) {
			if _, err := h.profile.boundRequest([]byte(selectedRequest("," + field))); err == nil {
				t.Fatal("unsupported/ambiguous reasoning accepted")
			}
		})
	}
}

func TestSelectedReasoningReachesOnlyOwnedUpstream(t *testing.T) {
	for _, field := range []string{`"reasoning_effort":"low"`, `"reasoning":{"effort":"low"}`} {
		h := selectedHandler(t)
		calls := 0
		h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			calls++
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Fatal(err)
			}
			var got map[string]any
			if err := json.Unmarshal(body, &got); err != nil {
				t.Fatal(err)
			}
			if got["reasoning_effort"] != "low" || got["max_tokens"] != float64(256) || got["reasoning"] != nil ||
				r.URL.String() != "http://127.0.0.1:8000/v1/chat/completions" {
				t.Fatal("native effort or owned upstream changed")
			}
			return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(`{"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}`))}, nil
		})
		r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(selectedRequest(","+field)))
		r.Header.Set("Authorization", "Bearer "+testKey)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 || calls != 1 || !strings.Contains(w.Body.String(), `"completion_tokens":2`) {
			t.Fatal("selected request/usage failed")
		}
	}
}

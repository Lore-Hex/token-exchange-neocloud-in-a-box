// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"go.yaml.in/yaml/v3"
)

const originalModel = "deepseek/deepseek-v4.1-flash-1017"

func TestOriginalProfileIdentityAndBounds(t *testing.T) {
	raw, err := os.ReadFile("../../models/deepseek-v4.1-flash-1017/1/pack.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var pack struct {
		CatalogID string `yaml:"tr_model_id"`
		Repo      string `yaml:"hf_repo"`
	}
	if yaml.Unmarshal(raw, &pack) != nil || pack.CatalogID != originalModel || pack.Repo != "deepseek-ai/DeepSeek-V4.1-Flash-1017" {
		t.Fatal("original profile disagrees with the official model pack")
	}
	p, err := profileFor(ProfileOriginalMillion)
	if err != nil || p.model != originalModel || p.name != "DeepSeek V4.1 Flash 1017" {
		t.Fatal("original profile identity mismatch")
	}
	selected, _ := profileFor(ProfileSelectedMillion)
	if selected.model != selectedModel {
		t.Fatal("legacy selected profile was reinterpreted")
	}
	selected.model, selected.name = p.model, p.name
	if p != selected || p.defaultOutput() != 2048 || p.bodyLimit() != 16<<20 || p.requestTimeout() != 3540*time.Second {
		t.Fatal("official model changed the existing bounded request policy")
	}
	for _, model := range []string{originalModel, selectedModel, "deepseek-v4.1-flash-1017", "deepseek-ai/DeepSeek-V4.1-Flash-1017"} {
		body := strings.ReplaceAll(selectedRequest(`,"max_tokens":393216`), selectedModel, model)
		got, err := p.boundRequest([]byte(body))
		if model != originalModel {
			if err == nil {
				t.Fatal("alternate checkpoint or alias accepted")
			}
			continue
		}
		var fields map[string]any
		if err != nil || json.Unmarshal(got, &fields) != nil || fields["model"] != originalModel || fields["max_tokens"] != float64(393216) {
			t.Fatal("original model ID or request limits changed")
		}
	}
}

func TestOriginalProfileLeaseCacheAndUsage(t *testing.T) {
	cutoff := time.Now().Add(6 * 24 * time.Hour).UTC().Format(time.RFC3339)
	cachePrice := "0.13"
	pricing := Pricing{Input: "0.65", Output: "1.45", CachedInput: &cachePrice}
	h, err := NewWithPricing(ProfileOriginalMillion, testProvider(), testKey, pricing, cutoff, "ordinary-7d")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{originalModel, `"owned_by":"deepseek"`, `"cached_input":"0.13"`, cutoff} {
		if !strings.Contains(string(h.catalog), want) {
			t.Fatal("official catalog lost identity, frozen price or cutoff")
		}
	}
	if cap(h.slots) != 1 || h.ShutdownGrace() != 3550*time.Second {
		t.Fatal("official profile weakened admission or drain")
	}
	wire := `{"model":"` + originalModel + `","usage":{"prompt_tokens":128,"completion_tokens":1,"total_tokens":129,"prompt_tokens_details":{"cached_tokens":64}}}`
	h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		body, err := io.ReadAll(r.Body)
		var got map[string]any
		if err != nil || json.Unmarshal(body, &got) != nil || got["model"] != originalModel || got["cache_salt"] == nil {
			t.Fatal("official request lost its exact identity or isolated cache scope")
		}
		rank := r.Header.Get("X-data-parallel-rank")
		if rank != "0" && rank != "1" {
			t.Fatal("cache affinity was not preserved")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/json"}}, Body: io.NopCloser(strings.NewReader(wire))}, nil
	})
	body := strings.ReplaceAll(selectedRequest(`,"cache_salt":"`+strings.Repeat("a", 64)+`"`), selectedModel, originalModel)
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 || w.Body.String() != wire {
		t.Fatal("official response or actual cache usage was rewritten")
	}
	for _, value := range []string{"", time.Now().Add(8 * 24 * time.Hour).UTC().Format(time.RFC3339)} {
		if _, err := NewWithPricing(ProfileOriginalMillion, testProvider(), testKey, pricing, value, "ordinary-7d"); err == nil {
			t.Fatal("official profile admitted an unbounded weekly lease")
		}
	}
	h.now = func() time.Time { return h.retirement }
	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/v1/models", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatal("expired official profile remained advertised")
	}
}

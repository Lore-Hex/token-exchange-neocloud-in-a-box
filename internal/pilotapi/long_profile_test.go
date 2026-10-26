// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func longHandler(t *testing.T) *Handler {
	t.Helper()
	h, err := NewWithProfile(ProfileSelectedLong, testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestLongProfileLimits(t *testing.T) {
	h := longHandler(t)
	if h.profile.context != 131072 || h.profile.maxOutput != 16384 || cap(h.slots) != 1 ||
		h.profile.bodyLimit() != 4<<20 {
		t.Fatal("long profile limits disagree")
	}
	transport := h.proxy.Transport.(*http.Transport)
	if transport.ResponseHeaderTimeout != 900*time.Second {
		t.Fatal("backend timeout disagrees with guard")
	}
	for _, want := range []string{`"context_length":131072`, `"max_output_tokens":16384`,
		`"completion_timeout_seconds":900`, `"prompt_caching":false`, `"cached_input":null`} {
		if !strings.Contains(string(h.catalog), want) {
			t.Fatalf("catalog missing %s", want)
		}
	}
	for _, item := range []struct {
		fields string
		want   int
	}{{"", 2048}, {`,"max_tokens":16384`, 16384}, {`,"max_completion_tokens":16384`, 16384},
		{`,"max_tokens":16384,"max_completion_tokens":4096`, 4096}} {
		body, err := h.profile.boundRequest([]byte(selectedRequest(item.fields)))
		var got map[string]any
		if err != nil || json.Unmarshal(body, &got) != nil || got["max_tokens"] != float64(item.want) || got["model"] != selectedModel {
			t.Fatalf("output bound failed: %s", item.fields)
		}
	}
	for _, fields := range []string{`,"max_tokens":16385`, `,"max_tokens":null`, `,"max_tokens":0`,
		`,"max_tokens":true`, `,"max_tokens":1,"max_completion_tokens":16385`} {
		if _, err := h.profile.boundRequest([]byte(selectedRequest(fields))); err == nil {
			t.Fatal("invalid output limit accepted")
		}
	}
	for _, name := range []string{ProfileQwen, ProfileSelectedDeepSeek, ProfileSelectedGraphs, ProfileSelectedQuality} {
		p, err := profileFor(name)
		if err != nil || p.bodyLimit() != 65536 || p.defaultOutput() != p.maxOutput {
			t.Fatal("existing profile changed")
		}
	}
}

func TestLongContextRequestBounds(t *testing.T) {
	p := longHandler(t).profile
	long := []byte(`{"model":"` + selectedModel + `","messages":[{"role":"user","content":"` + strings.Repeat("x", 512<<10) + `"}]}`)
	if _, err := p.boundRequest(long); err != nil {
		t.Fatal("long text rejected", err)
	}
	if _, err := selectedHandler(t).profile.boundRequest(long); err == nil {
		t.Fatal("default body bound widened")
	}
	for _, bad := range [][]byte{append(long, bytes.Repeat([]byte(" "), p.bodyLimit())...),
		[]byte(selectedRequest(`,"cache_salt":"` + strings.Repeat("a", 64) + `","cache_salt":"` + strings.Repeat("b", 64) + `"`)),
		[]byte(`{"model":"` + selectedModel + `","messages":[{"role":"user","content":"x","content":"y"}]}`)} {
		if _, err := p.boundRequest(bad); err == nil {
			t.Fatal("oversize or ambiguous request accepted")
		}
	}
	if boundedToolJSON([]byte(strings.Repeat("[", 34)+"0"+strings.Repeat("]", 34)), p.bodyLimit()) {
		t.Fatal("long profile weakened nesting bound")
	}
}

func TestLongCacheIsolation(t *testing.T) {
	p := longHandler(t).profile
	salt := func(fields string) string {
		t.Helper()
		body, err := p.boundRequest([]byte(selectedRequest(fields)))
		var got map[string]any
		if err != nil || json.Unmarshal(body, &got) != nil {
			t.Fatal("salt request failed", err)
		}
		return got["cache_salt"].(string)
	}
	a := `,"cache_salt":"` + strings.Repeat("a", 64) + `"`
	b := `,"cache_salt":"` + strings.Repeat("b", 64) + `"`
	first, repeated, other := salt(a), salt(a), salt(b)
	anonymous, nextAnonymous := salt(""), salt("")
	if first != repeated || first == other || anonymous == nextAnonymous || first == strings.Repeat("a", 64) {
		t.Fatal("cache scope not stable, isolated and domain-separated")
	}
	for _, raw := range []string{`null`, `true`, `1`, `[]`, `""`, `"abc"`, `"` + strings.Repeat("A", 64) + `"`, `"` + strings.Repeat("z", 64) + `"`} {
		if _, err := p.boundRequest([]byte(selectedRequest(`,"cache_salt":` + raw))); err == nil {
			t.Fatal("invalid namespace accepted", raw)
		}
	}
	if _, err := selectedHandler(t).profile.boundRequest([]byte(selectedRequest(a))); err == nil {
		t.Fatal("cache option escaped explicit profile")
	}
}

type longRoundTripper func(*http.Request) (*http.Response, error)

func TestProfileDeadlineAndDrainBounds(t *testing.T) {
	for _, name := range []string{ProfileQwen, ProfileSelectedDeepSeek, ProfileSelectedGraphs, ProfileSelectedQuality, ProfileSelectedLong} {
		p, err := profileFor(name)
		if err != nil {
			t.Fatal(err)
		}
		want := 120 * time.Second
		if name == ProfileSelectedLong {
			want = 900 * time.Second
		}
		h := &Handler{profile: p}
		if p.requestTimeout() != want || h.ShutdownGrace() != want+10*time.Second {
			t.Fatalf("profile %s deadline/drain mismatch", name)
		}
	}
}

func (f longRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestLongHTTPBoundsAndUsagePassthrough(t *testing.T) {
	h := longHandler(t)
	calls := 0
	wire := `{"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":1,"total_tokens":101,"prompt_tokens_details":{"cached_tokens":64}}}`
	h.proxy.Transport = longRoundTripper(func(r *http.Request) (*http.Response, error) {
		calls++
		deadline, ok := r.Context().Deadline()
		if !ok || time.Until(deadline) < 890*time.Second || time.Until(deadline) > 900*time.Second {
			t.Error("long inference deadline not enforced")
		}
		body, err := io.ReadAll(r.Body)
		if err != nil || !bytes.Contains(body, []byte(`"cache_salt":`)) || !bytes.Contains(body, []byte(`"max_tokens":16384`)) {
			t.Error("cache namespace or output limit not forwarded")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/json"}}, Body: io.NopCloser(strings.NewReader(wire))}, nil
	})
	request := func(body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+testKey)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	body := `{"model":"` + selectedModel + `","messages":[{"role":"user","content":"` + strings.Repeat("x", 512<<10) + `"}],"max_tokens":16384}`
	if w := request(body); w.Code != 200 || w.Body.String() != wire || calls != 1 {
		t.Fatal("long request or genuine cache usage changed")
	}
	if w := request(body + strings.Repeat(" ", h.profile.bodyLimit())); w.Code != 413 || calls != 1 {
		t.Fatal("oversize HTTP request reached backend")
	}
}

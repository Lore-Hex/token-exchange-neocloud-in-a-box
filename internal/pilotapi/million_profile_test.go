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

func TestMillionProfileLimits(t *testing.T) {
	h := longProfileHandler(t, ProfileSelectedMillion)
	if h.profile.context != 1048576 || h.profile.maxOutput != 393216 || cap(h.slots) != 1 || h.profile.bodyLimit() != 16<<20 {
		t.Fatal("million profile limits disagree")
	}
	if h.proxy.Transport.(*http.Transport).ResponseHeaderTimeout != 3540*time.Second || h.ShutdownGrace() != 3550*time.Second {
		t.Fatal("million transport deadlines disagree")
	}
	for _, want := range []string{`"context_length":1048576`, `"max_output_tokens":393216`,
		`"completion_timeout_seconds":3540`, `"prompt_caching":false`, `"cached_input":null`} {
		if !strings.Contains(string(h.catalog), want) {
			t.Fatalf("catalog missing %s", want)
		}
	}
	for _, item := range []struct {
		fields string
		want   int
	}{{"", 2048}, {`,"max_tokens":393216`, 393216}, {`,"max_completion_tokens":393216`, 393216},
		{`,"max_tokens":393216,"max_completion_tokens":4096`, 4096}} {
		body, err := h.profile.boundRequest([]byte(selectedRequest(item.fields)))
		var got map[string]any
		if err != nil || json.Unmarshal(body, &got) != nil || got["max_tokens"] != float64(item.want) || got["model"] != selectedModel {
			t.Fatalf("output bound failed: %s", item.fields)
		}
	}
	for _, fields := range []string{`,"max_tokens":393217`, `,"max_tokens":null`, `,"max_tokens":0`,
		`,"max_tokens":true`, `,"max_tokens":1,"max_completion_tokens":393217`} {
		if _, err := h.profile.boundRequest([]byte(selectedRequest(fields))); err == nil {
			t.Fatal("invalid output limit accepted")
		}
	}
}

func TestMillionHTTPBoundsAndUsagePassthrough(t *testing.T) {
	h := longProfileHandler(t, ProfileSelectedMillion)
	calls := 0
	wire := `{"choices":[],"usage":{"prompt_tokens":655360,"completion_tokens":393216,"total_tokens":1048576,"prompt_tokens_details":{"cached_tokens":655296}}}`
	h.proxy.Transport = longRoundTripper(func(r *http.Request) (*http.Response, error) {
		calls++
		deadline, ok := r.Context().Deadline()
		if !ok || time.Until(deadline) < 3530*time.Second || time.Until(deadline) > 3540*time.Second {
			t.Error("million inference deadline not enforced")
		}
		body, err := io.ReadAll(r.Body)
		if err != nil || !bytes.Contains(body, []byte(`"cache_salt":`)) || !bytes.Contains(body, []byte(`"max_tokens":393216`)) {
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
	body := `{"model":"` + selectedModel + `","messages":[{"role":"user","content":"` + strings.Repeat("x", 5<<20) + `"}],"max_tokens":393216}`
	if w := request(body); w.Code != 200 || w.Body.String() != wire || calls != 1 {
		t.Fatal("million request or genuine usage changed")
	}
	if _, err := longHandler(t).profile.boundRequest([]byte(body)); err == nil {
		t.Fatal("legacy profile body bound widened")
	}
	if w := request(body + strings.Repeat(" ", h.profile.bodyLimit())); w.Code != 413 || calls != 1 {
		t.Fatal("oversize HTTP request reached backend")
	}
	h.slots <- struct{}{}
	defer func() { <-h.slots }()
	if w := request(selectedRequest("")); w.Code != http.StatusTooManyRequests || calls != 1 {
		t.Fatal("million profile admitted concurrent inference")
	}
}

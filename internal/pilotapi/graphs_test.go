// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestGraphProfileOnlyChangesContextAndAdmission(t *testing.T) {
	eager, _ := profileFor(ProfileSelectedDeepSeek)
	graphs, err := profileFor(ProfileSelectedGraphs)
	if err != nil || graphs.context != 4096 || graphs.concurrent != 8 || graphs.maxOutput != 256 {
		t.Fatal("graph limits drifted")
	}
	graphs.context, graphs.concurrent = eager.context, eager.concurrent
	if graphs != eager {
		t.Fatal("graph profile changed identity or request/capability behavior")
	}
	h, err := NewWithProfile(ProfileSelectedGraphs, testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Replace(string(selectedHandler(t).catalog), `"context_length":8192`, `"context_length":4096`, 1)
	if string(h.catalog) != want {
		t.Fatal("graph catalog changed more than context")
	}
	for _, fields := range []string{"", `,"max_tokens":256`, `,"max_tokens":257`,
		`,"reasoning_effort":"none"`, `,"tools":[]`, `,"n":2`, `,"stream":true`} {
		left, leftErr := eager.boundRequest([]byte(selectedRequest(fields)))
		right, rightErr := h.profile.boundRequest([]byte(selectedRequest(fields)))
		if string(left) != string(right) || (leftErr == nil) != (rightErr == nil) {
			t.Fatal("graph request parser diverged")
		}
	}
}

func TestGraphEightStreamsNinthRejectedCancellationHealthAndRetirement(t *testing.T) {
	cutoff := time.Now().Add(time.Hour).UTC().Truncate(time.Second)
	h, err := NewWithProfile(ProfileSelectedGraphs, testProvider(), testKey, "0.10", "0.20", cutoff.Format(time.RFC3339))
	if err != nil {
		t.Fatal(err)
	}
	var retired atomic.Bool
	h.now = func() time.Time {
		if retired.Load() {
			return cutoff
		}
		return cutoff.Add(-time.Minute)
	}
	started := make(chan *io.PipeWriter, 8)
	var calls atomic.Int32
	h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path == "/health" {
			return &http.Response{StatusCode: 200, Header: http.Header{}, Body: http.NoBody}, nil
		}
		calls.Add(1)
		reader, writer := io.Pipe()
		context.AfterFunc(r.Context(), func() { _ = reader.CloseWithError(r.Context().Err()) })
		started <- writer
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream"}}, Body: reader}, nil
	})
	request := func(ctx context.Context) *http.Request {
		r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(selectedRequest(`,"stream":true`))).WithContext(ctx)
		r.Header.Set("Authorization", "Bearer "+testKey)
		return r
	}
	done := make(chan *httptest.ResponseRecorder, 8)
	cancels := make([]context.CancelFunc, 8)
	writers := make([]*io.PipeWriter, 8)
	for index := range 8 {
		ctx, cancel := context.WithCancel(context.Background())
		cancels[index] = cancel
		defer cancel()
		go func() {
			w := httptest.NewRecorder()
			h.ServeHTTP(w, request(ctx))
			done <- w
		}()
		select {
		case writers[index] = <-started:
			defer writers[index].Close()
		case <-time.After(5 * time.Second):
			t.Fatal("stream did not enter upstream")
		}
	}
	ninth := httptest.NewRecorder()
	h.ServeHTTP(ninth, request(context.Background()))
	if ninth.Code != 429 || ninth.Header().Get("Retry-After") != "1" || calls.Load() != 8 || len(h.slots) != 8 {
		t.Fatal("ninth request queued/reached engine or admission released before EOF")
	}
	health := httptest.NewRecorder()
	h.ServeHTTP(health, httptest.NewRequest("GET", "/health", nil))
	if health.Code != 200 || len(h.slots) != 8 {
		t.Fatal("health competed for generation slots")
	}
	cancels[0]()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled stream did not release its reader/admission")
	}
	if len(h.slots) != 7 {
		t.Fatal("cancelled stream leaked a slot")
	}
	retired.Store(true)
	late := httptest.NewRecorder()
	h.ServeHTTP(late, request(context.Background()))
	if late.Code != 503 || calls.Load() != 8 {
		t.Fatal("retirement admitted a new request")
	}
	const tail = "data: {\"usage\":{\"prompt_tokens\":12,\"completion_tokens\":3,\"total_tokens\":15}}\n\ndata: [DONE]\n\n"
	for _, writer := range writers[1:] {
		if _, err := io.WriteString(writer, tail); err != nil {
			t.Fatal(err)
		}
		_ = writer.Close()
	}
	for range 7 {
		select {
		case response := <-done:
			if response.Code != 200 || response.Body.String() != tail {
				t.Fatal("retirement changed active stream/usage bytes")
			}
		case <-time.After(5 * time.Second):
			t.Fatal("active stream did not drain")
		}
	}
	if len(h.slots) != 0 {
		t.Fatal("drain leaked admission")
	}
}

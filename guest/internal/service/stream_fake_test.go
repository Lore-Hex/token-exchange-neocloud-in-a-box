//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestFakeForwardedFields(t *testing.T) {
	f := newFixture(t)
	h := f.session().fakeHandler(func() { t.Error("gate failed") })
	for _, field := range []string{`"max_tokens":4`, `"max_completion_tokens":4`, `"temperature":0`, `"top_p":0.9`, `"top_k":40`, `"thinking":{"type":"disabled"}`, `"reasoning":{"effort":"low"}`, `"reasoning_effort":"low"`} {
		w := httptest.NewRecorder()
		body := strings.Replace(chatBody, `"model":`, field+`,"model":`, 1)
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
		if w.Code != 200 || !strings.Contains(w.Body.String(), `"content":"PONG"`) || strings.Contains(w.Body.String(), "SECRET") {
			t.Fatalf("field rejected: %s status=%d", field, w.Code)
		}
	}
	for _, field := range []string{`"max_tokens":0`, `"max_tokens":65537`, `"max_tokens":1.5`, `"max_tokens":"4"`, `"temperature":-1`, `"temperature":3`, `"top_p":2`, `"top_k":-1`, `"unknown":true`} {
		w := httptest.NewRecorder()
		body := strings.Replace(chatBody, `"model":`, field+`,"model":`, 1)
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
		if w.Code != 400 {
			t.Fatalf("invalid field accepted: %s", field)
		}
	}
}

func TestFakeUnavailableBeforeStreaming(t *testing.T) {
	f := newFixture(t)
	h := f.session().fakeHandler(func() { t.Error("gate failed") })
	w := httptest.NewRecorder()
	body := strings.Replace(chatBody, "nibfake-model", "nibfake-unavailable", 1)
	body = strings.Replace(body, `"model":`, `"stream":true,"model":`, 1)
	h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
	if w.Code != 503 || w.Header().Get("Retry-After") != "7" || !json.Valid(w.Body.Bytes()) || strings.Contains(w.Body.String(), "data:") {
		t.Fatal("synthetic overload must fail before streaming")
	}
}

func TestFakeStreamIncrementalAndInterrupted(t *testing.T) {
	for _, mode := range []string{"complete", "gate-loss", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			f := newFixture(t)
			server := httptest.NewServer(f.session().fakeHandler(func() {}))
			defer server.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			body := strings.Replace(chatBody, `"model":`, `"stream":true,"stream_options":{"include_usage":true},"model":`, 1)
			r, err := http.NewRequestWithContext(ctx, "POST", server.URL+"/v1/chat/completions", strings.NewReader(body))
			if err != nil {
				t.Fatal(err)
			}
			resp, err := server.Client().Do(r)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			scanner := bufio.NewScanner(resp.Body)
			text, done, usage := "", false, false
			var first, last time.Time
			for scanner.Scan() {
				line := scanner.Text()
				if line == "data: [DONE]" {
					done = true
					continue
				}
				if !strings.HasPrefix(line, "data: ") {
					continue
				}
				var chunk struct {
					Choices []struct {
						Delta struct {
							Content string `json:"content"`
						} `json:"delta"`
					} `json:"choices"`
					Usage *struct {
						Total int `json:"total_tokens"`
					} `json:"usage"`
				}
				if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
					t.Fatal(err)
				}
				if chunk.Usage != nil {
					usage = chunk.Usage.Total == 2
				}
				if len(chunk.Choices) == 0 || chunk.Choices[0].Delta.Content == "" {
					continue
				}
				text += chunk.Choices[0].Delta.Content
				last = time.Now()
				if first.IsZero() {
					first = last
					if mode == "gate-loss" {
						f.write("weights.bin", []byte("corrupted"))
					}
					if mode == "cancel" {
						cancel()
					}
				}
			}
			if mode == "complete" {
				if scanner.Err() != nil || text != "PONG" || !done || !usage || last.Sub(first) < 60*time.Millisecond {
					t.Fatal("stream buffered, incomplete or missing usage")
				}
			} else if done || usage || text == "PONG" {
				t.Fatal("interrupted stream completed")
			}
		})
	}
}

//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

//go:noinline
func nibfakeEngine(ctx context.Context, s *fakeSession, out io.Writer) error {
	if err := s.checkGates(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	listener, err := net.Listen("tcp4", s.config.EngineAddress)
	if err != nil {
		return errors.New("simulated engine listener unavailable")
	}
	defer listener.Close()
	failure := make(chan struct{})
	var once sync.Once
	fail := func() { once.Do(func() { close(failure) }) }
	server := &http.Server{
		Handler: s.fakeHandler(fail), ReadHeaderTimeout: 2 * time.Second,
		ReadTimeout: 3 * time.Second, WriteTimeout: 3 * time.Second,
		IdleTimeout: 5 * time.Second, MaxHeaderBytes: 8192,
		ErrorLog: log.New(io.Discard, "", 0),
	}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	defer func() { server.Close(); <-done }()
	if _, err := fmt.Fprintln(out, "NIBFAKE: synthetic engine ready on loopback; not confidential"); err != nil {
		return errors.New("status output failed")
	}
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-failure:
			return errors.New("simulated gate lost")
		case err := <-done:
			// Preserve the value for the deferred join.
			done <- err
			return errors.New("simulated engine stopped")
		case <-ticker.C:
			if s.checkGates() != nil {
				return errors.New("simulated gate lost")
			}
		}
	}
}

func (s *fakeSession) fakeHandler(fail func()) http.Handler {
	// Bound concurrent handlers; the fixture server is not a load-test engine.
	slots := make(chan struct{}, 8)
	var failed atomic.Bool
	check := func(w http.ResponseWriter) bool {
		if !failed.Load() && s.checkGates() == nil {
			return true
		}
		failed.Store(true)
		w.Header().Set("Retry-After", "1")
		w.WriteHeader(http.StatusServiceUnavailable)
		io.WriteString(w, `{"error":"simulation gate unavailable"}`)
		fail()
		return false
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Nib-Simulation", "nibfake")
		select {
		case slots <- struct{}{}:
			defer func() { <-slots }()
		default:
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			io.WriteString(w, `{"error":"simulation capacity"}`)
			return
		}
		if !check(w) {
			return
		}
		if r.URL.RawQuery != "" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		switch {
		case r.Method == "GET" && r.URL.Path == "/health":
			io.WriteString(w, `{"status":"simulated","hardware_validated":false}`)
		case r.Method == "GET" && r.URL.Path == "/v1/models":
			io.WriteString(w, `{"object":"list","data":[{"id":"nibfake-model","object":"model","owned_by":"nibfake"}]}`)
		case r.Method == "POST" && r.URL.Path == "/v1/chat/completions":
			r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
			defer r.Body.Close()
			b, err := io.ReadAll(r.Body)
			if err != nil {
				w.WriteHeader(http.StatusRequestEntityTooLarge)
				return
			}
			var request struct {
				Model         string `json:"model"`
				Stream        bool   `json:"stream"`
				StreamOptions struct {
					IncludeUsage bool `json:"include_usage"`
				} `json:"stream_options"`
				Messages []struct {
					Role    string `json:"role"`
					Content string `json:"content"`
				} `json:"messages"`
			}
			if strictJSON(b, &request) != nil || request.Model != "nibfake-model" || len(request.Messages) == 0 || len(request.Messages) > 32 {
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			for _, message := range request.Messages {
				if message.Role != "system" && message.Role != "user" && message.Role != "assistant" {
					w.WriteHeader(http.StatusBadRequest)
					return
				}
			}
			// A slow request must not serve after a gate changed during its read.
			if !check(w) {
				return
			}
			if request.Stream {
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, "data: {\"id\":\"nibfake\",\"object\":\"chat.completion.chunk\",\"model\":\"nibfake-model\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"SYNTHETIC\"},\"finish_reason\":null}]}\n\n")
				http.NewResponseController(w).Flush()
				io.WriteString(w, "data: {\"id\":\"nibfake\",\"object\":\"chat.completion.chunk\",\"model\":\"nibfake-model\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n")
				if request.StreamOptions.IncludeUsage {
					io.WriteString(w, "data: {\"id\":\"nibfake\",\"object\":\"chat.completion.chunk\",\"model\":\"nibfake-model\",\"choices\":[],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"total_tokens\":2}}\n\n")
				}
				io.WriteString(w, "data: [DONE]\n\n")
				return
			}
			json.NewEncoder(w).Encode(map[string]any{
				"id": "nibfake", "object": "chat.completion", "model": "nibfake-model",
				"usage":   map[string]int{"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
				"choices": []any{map[string]any{"index": 0, "message": map[string]string{"role": "assistant", "content": "SYNTHETIC"}, "finish_reason": "stop"}},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	})
}

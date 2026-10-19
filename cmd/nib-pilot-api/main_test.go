// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"testing"
	"time"
)

func TestShutdownWaitsForActiveResponse(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, release := make(chan struct{}), make(chan struct{})
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-release
		_, _ = io.WriteString(w, "complete")
	})}
	defer server.Close()
	done := make(chan error, 1)
	go func() { done <- serve(ctx, server, listener) }()
	response := make(chan string, 1)
	go func() {
		client := &http.Client{Timeout: 5 * time.Second}
		result, err := client.Get("http://" + listener.Addr().String())
		if err != nil {
			response <- "failed"
			return
		}
		defer result.Body.Close()
		body, _ := io.ReadAll(result.Body)
		response <- string(body)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("request did not start")
	}
	cancel()
	select {
	case err := <-done:
		close(release)
		t.Fatalf("exited before draining: %v", err)
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	if got := <-response; got != "complete" {
		t.Fatalf("interrupted response: %q", got)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("server did not stop after draining")
	}
}

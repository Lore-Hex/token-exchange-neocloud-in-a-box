//go:build (linux || darwin) && !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"testing"
	"time"
)

func TestNetworkProcessBounds(t *testing.T) {
	out, err := executeNetwork(context.Background(), "/bin/cat", nil, []byte("fixed input\n"))
	if err != nil || string(out) != "fixed input\n" {
		t.Fatal("bounded stdin roundtrip failed")
	}
	for i, command := range []string{"printf detail >&2", "exit 1", "sleep 10 & exit 0", "head -c 65537 /dev/zero"} {
		start := time.Now()
		out, err := executeNetwork(context.Background(), "/bin/sh", []string{"-c", command}, nil)
		if err == nil || out != nil || time.Since(start) > 3*time.Second {
			t.Fatalf("synthetic case %d: error=%v bytes=%d duration=%s", i, err != nil, len(out), time.Since(start))
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	start := time.Now()
	if out, err := executeNetwork(ctx, "/bin/sleep", []string{"10"}, nil); err == nil || out != nil || time.Since(start) > 3*time.Second {
		t.Fatal("cancellation did not reap process")
	}
	var b networkOutput
	if _, err := b.Write(bytes.Repeat([]byte("x"), 65536)); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write([]byte("x")); err == nil || b.buffer.Len() != 65536 {
		t.Fatal("output cap weakened")
	}
}

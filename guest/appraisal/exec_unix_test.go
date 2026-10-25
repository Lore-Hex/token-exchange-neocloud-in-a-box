//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestProcessBoundsAndFailures(t *testing.T) {
	for _, script := range []string{"printf synthetic", "printf '" + canary + "' >&2", "exit 1", "sleep 10", "sleep 10 & exit 0", "head -c 65537 /dev/zero"} {
		t.Run(script, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
			defer cancel()
			start := time.Now()
			raw, gone, err := execute(ctx, "/bin/sh", []string{"-c", script}, []string{"PATH=/usr/bin:/bin"})
			if script == "printf synthetic" {
				if err != nil || !gone || string(raw) != "synthetic" {
					t.Fatal(err)
				}
			} else if err == nil || raw != nil || strings.Contains(err.Error(), canary) {
				t.Fatal("failure escaped", err)
			}
			if time.Since(start) > 3*time.Second {
				t.Fatal("unbounded subprocess")
			}
		})
	}
}

func TestCancellationAndOutputCap(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	timer := time.AfterFunc(50*time.Millisecond, cancel)
	defer timer.Stop()
	if raw, _, err := execute(ctx, "/bin/sh", []string{"-c", "sleep 10"}, []string{"PATH=/usr/bin:/bin"}); err == nil || raw != nil {
		t.Fatal("cancelled child accepted")
	}
	called := false
	b := boundedOutput{cancel: func() { called = true }}
	if _, err := b.Write(make([]byte, limit)); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write([]byte("x")); err == nil || !called || b.buffer.Len() != limit {
		t.Fatal("cap weakened")
	}
}

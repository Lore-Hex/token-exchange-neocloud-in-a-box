//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVerifierFakeCodeAbsentFromReleaseBinary(t *testing.T) {
	env := append(os.Environ(), "GOFLAGS=", "GOWORK=off", "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0")
	run := func(args ...string) string {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
		defer cancel()
		cmd := exec.CommandContext(ctx, "go", args...)
		cmd.Env = env
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("Go binary check failed: %v\n%s", err, out)
		}
		return string(out)
	}
	for _, tags := range []string{"", "nibfake"} {
		binary := filepath.Join(t.TempDir(), "nib")
		run("build", "-trimpath", "-tags="+tags, "-o", binary, ".")
		symbols := run("tool", "nm", binary)
		if tags == "" {
			for _, forbidden := range []string{"nibfake", ".FakeTDXQuote", ".FakeDeviceEvidence"} {
				if strings.Contains(symbols, forbidden) {
					t.Fatalf("fake verifier linked into release: %s", forbidden)
				}
			}
		} else {
			for _, required := range []string{"/internal/verify.nibfakeAppraiseCPU", "/internal/verify.nibfakeAppraiseDevices"} {
				if !strings.Contains(symbols, required) {
					t.Fatalf("fake positive control missing: %s", required)
				}
			}
		}
	}
}

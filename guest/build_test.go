//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package guest_test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func goCommand(t *testing.T, env []string, args ...string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "go", args...)
	cmd.Env = append(os.Environ(), env...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("go %v: %v\n%s", args, err, out)
	}
	return string(out)
}

// The untagged suite is CI's entrypoint. This tagged child cannot recurse,
// because this file is excluded from nibfake builds.
func TestTaggedGuestSuite(t *testing.T) {
	env := []string{"GOFLAGS=", "GOWORK=off", "GOOS=" + runtime.GOOS, "GOARCH=" + runtime.GOARCH, "CGO_ENABLED=1"}
	t.Log(goCommand(t, env, "test", "-race", "-p=1", "-tags=nibfake", "-count=1", "./..."))
}

func TestReleaseSymbolsExcludeFakes(t *testing.T) {
	env := []string{"GOFLAGS=", "GOWORK=off", "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0"}
	for _, tags := range []string{"", "nibfake"} {
		dir := t.TempDir()
		goCommand(t, env, "build", "-trimpath", "-tags="+tags, "-o", dir+string(os.PathSeparator), "./cmd/...")
		for _, helper := range []string{"nib-gpu-gate", "nib-weights", "nib-network", "nib-engine"} {
			symbols := goCommand(t, env, "tool", "nm", filepath.Join(dir, helper))
			if tags == "" {
				if strings.Contains(strings.ToLower(symbols), "nibfake") {
					t.Fatalf("%s release contains fake symbols", helper)
				}
			} else {
				// Positive controls make a stripped/empty or ineffective scan fail.
				for _, symbol := range []string{"nibfakeGPU", "nibfakeWeights", "nibfakeNetwork", "nibfakeEngine"} {
					if !strings.Contains(symbols, "/guest/internal/service."+symbol) {
						t.Fatalf("%s tagged build missing positive control %s", helper, symbol)
					}
				}
			}
		}
	}
}

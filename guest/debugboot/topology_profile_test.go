//go:build (linux || darwin) && !nibfake

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"context"
	"os"
	"os/exec"
	"runtime"
	"testing"
	"time"
)

// Exercise opt-in staging in ordinary CI without executing the Linux controller.
func TestTopologyProfileTaggedStaging(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "go", "test", "-race", "-tags=nibdebugtopology", "-run=^TestTopologyStage", ".")
	cmd.Env = append(os.Environ(), "GOOS="+runtime.GOOS, "GOARCH="+runtime.GOARCH, "CGO_ENABLED=1",
		"GOTOOLCHAIN=local", "GOPROXY=off", "GOSUMDB=off", "GOWORK=off")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("tagged staging: %v: %s", err, out)
	}
}

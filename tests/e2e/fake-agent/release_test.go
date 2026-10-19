//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"context"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestReleaseDeniesAndSymbols(t *testing.T) {
	var out bytes.Buffer
	if run(context.Background(), []string{"gpu"}, &out) == nil || out.Len() != 0 {
		t.Fatal("release collector must deny")
	}
	for _, fake := range []bool{false, true} {
		binary := filepath.Join(t.TempDir(), "collector")
		args := []string{"build", "-o", binary}
		if fake {
			args = append(args, "-tags", "nibfake")
		}
		args = append(args, ".")
		if b, err := exec.Command("go", args...).CombinedOutput(); err != nil {
			t.Fatalf("build: %v: %s", err, b)
		}
		symbols, err := exec.Command("go", "tool", "nm", binary).CombinedOutput()
		if err != nil {
			t.Fatal(err)
		}
		for _, marker := range []string{"main.nibfakeAA", "main.nibfakeGPU"} {
			if bytes.Contains(symbols, []byte(marker)) != fake {
				t.Fatalf("symbol inclusion mismatch: %s fake=%v", marker, fake)
			}
		}
		if !fake && exec.Command(binary, "gpu").Run() == nil {
			t.Fatal("release executable accepted collection")
		}
	}
}

//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"path/filepath"
	"runtime"
	"testing"
)

func TestReleaseNeverAcceptsFixtures(t *testing.T) {
	path, err := filepath.Abs("../../testdata/fake/guest.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, role := range []string{"gpu-gate", "weights", "network", "engine"} {
		t.Run(role, func(t *testing.T) {
			t.Setenv("NIB_FAKE", "1")
			t.Setenv("NIB_SKIP_VERIFY", "1")
			var out, errs bytes.Buffer
			want := 1
			if role == "network" && runtime.GOOS == "linux" {
				want = int(failedNetworkPath)
			}
			if Run(context.Background(), role, []string{"--config", path}, &out, &errs) != want || out.Len() != 0 {
				t.Fatal("release accepted simulated input or emitted success")
			}
		})
	}
}

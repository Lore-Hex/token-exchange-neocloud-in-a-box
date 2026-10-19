// SPDX-License-Identifier: Apache-2.0
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNoOverwrite(t *testing.T) {
	p := filepath.Join(t.TempDir(), "artifact")
	if err := writeExclusive(p, []byte("first")); err != nil {
		t.Fatal(err)
	}
	if err := writeExclusive(p, []byte("second")); err == nil {
		t.Fatal("overwrote evidence")
	}
	b, err := os.ReadFile(p)
	if err != nil || string(b) != "first" {
		t.Fatal("changed evidence")
	}
}
func TestRejectArbitraryArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"SECRET_PATH"}, {"generate", "SECRET_PATH"}, {"--out", "SECRET_PATH"}} {
		if err := run(args); err == nil || strings.Contains(err.Error(), "SECRET_PATH") {
			t.Fatal("arguments accepted or leaked")
		}
	}
}

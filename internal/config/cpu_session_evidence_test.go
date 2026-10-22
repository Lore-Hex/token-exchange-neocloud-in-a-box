// SPDX-License-Identifier: Apache-2.0

package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOriginalCPUSessionEnvelopeSchemaBoundary(t *testing.T) {
	root := filepath.Join("..", "..", "evidence", "p2", "2026-10-22-cpu-session-parity-failure")
	for _, test := range []struct {
		name  string
		valid bool
	}{
		{"controller-run.json", false},
		{filepath.Join("pair", "run.json"), true},
	} {
		t.Run(test.name, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(root, test.name))
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateEvidence(raw); (err == nil) != test.valid {
				t.Fatalf("original envelope validity = %v, want %v: %v", err == nil, test.valid, err)
			}
		})
	}
}

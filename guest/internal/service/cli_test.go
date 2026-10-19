// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestArgumentAndErrorPrivacy(t *testing.T) {
	for _, tc := range []struct {
		role string
		args []string
		code int
	}{
		{"bad-SECRET", nil, 2}, {"gpu-gate", nil, 2}, {"engine", []string{"--fake", "SECRET"}, 2},
		{"engine", []string{"--config", "SECRET", "--pass"}, 2},
		{"engine", []string{"--config", "/missing/SECRET"}, 1},
	} {
		var out, errs bytes.Buffer
		if code := Run(context.Background(), tc.role, tc.args, &out, &errs); code != tc.code {
			t.Fatalf("unexpected status: %d", code)
		}
		if strings.Contains(out.String()+errs.String(), "SECRET") || out.Len() != 0 {
			t.Fatal("input leaked or unexpected positive output")
		}
	}
}

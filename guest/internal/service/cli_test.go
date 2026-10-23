// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"errors"
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

type testExitFailure int

func (f testExitFailure) Error() string        { return "PRIVATE ERROR" }
func (f testExitFailure) serviceExitCode() int { return int(f) }

func TestFailureExitStatusIsRestrictedToNetworkAndFixedRange(t *testing.T) {
	for _, role := range []string{"network", "gpu-gate", "weights", "engine", "unknown"} {
		for status := -1; status <= 256; status++ {
			want := 1
			if role == "network" && status >= 64 && status <= 93 {
				want = status
			}
			if failureExitStatus(role, testExitFailure(status)) != want {
				t.Fatal("unexpected diagnostic role or status")
			}
		}
	}
	for _, err := range []error{nil, errors.New("PRIVATE ERROR")} {
		if failureExitStatus("network", err) != 1 {
			t.Fatal("untyped error accepted")
		}
	}
}

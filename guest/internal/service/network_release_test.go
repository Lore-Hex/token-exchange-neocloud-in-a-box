//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"
)

func TestNetworkLifecycle(t *testing.T) {
	after, err := os.ReadFile("../../nftables/testdata/readback.json")
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string][]any
	if err := json.Unmarshal(after, &parsed); err != nil {
		t.Fatal(err)
	}
	before, _ := json.Marshal(map[string]any{"nftables": parsed["nftables"][:1]})
	config := []byte(`{"schema":"nib.guest-network/v1","egress":[]}`)
	for _, scenario := range []string{"pass", "dirty", "read-error", "check-error", "create-error", "final-error", "check-output", "create-output", "changed", "cancelled"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			calls := 0
			var checkInput []byte
			err := applyNetwork(ctx, config, func(ctx context.Context, op networkOperation, input []byte) ([]byte, error) {
				calls++
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > networkDeadline {
					t.Fatal("missing total budget")
				}
				want := []networkOperation{networkRead, networkCheck, networkCreate, networkRead}
				if calls > len(want) || op != want[calls-1] {
					t.Fatal("unexpected command/retry/rollback")
				}
				if scenario == []string{"read-error", "check-error", "create-error", "final-error"}[calls-1] {
					return nil, errors.New("synthetic detail not for logs")
				}
				if calls == 1 {
					if input != nil {
						t.Fatal("read received policy input")
					}
					if scenario == "dirty" {
						return after, nil
					}
					return before, nil
				}
				if op == networkCheck || op == networkCreate {
					if !bytes.Contains(input, []byte("create table inet nib {")) {
						t.Fatal("not create-only")
					}
					if op == networkCheck {
						checkInput = append([]byte(nil), input...)
					} else if !bytes.Equal(checkInput, input) {
						t.Fatal("check/create bytes differ")
					}
					if (scenario == "check-output" && op == networkCheck) || (scenario == "create-output" && op == networkCreate) {
						return []byte("unexpected"), nil
					}
					return nil, nil
				}
				if scenario == "changed" {
					return before, nil
				}
				if scenario == "cancelled" {
					cancel()
				}
				return after, nil
			})
			if (err == nil) != (scenario == "pass") {
				t.Fatal("incorrect acceptance")
			}
			expectedCalls := map[string]int{"pass": 4, "dirty": 1, "read-error": 1, "check-error": 2,
				"create-error": 3, "final-error": 4, "check-output": 2, "create-output": 3, "changed": 4, "cancelled": 4}
			if calls != expectedCalls[scenario] {
				t.Fatal("failure did not stop subsequent operations")
			}
		})
	}
	for _, raw := range [][]byte{nil, []byte(`{"schema":"nib.guest-fake/v1","egress":[]}`)} {
		if applyNetwork(context.Background(), raw, func(context.Context, networkOperation, []byte) ([]byte, error) {
			t.Fatal("invalid config reached executor")
			return nil, nil
		}) == nil {
			t.Fatal("invalid config passed")
		}
	}
}

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
			stages := map[string]networkFailure{"dirty": failedNetworkInitialRuleset, "read-error": failedNetworkInitialCommand,
				"check-error": failedNetworkCheck, "check-output": failedNetworkCheck, "create-error": failedNetworkCreate,
				"create-output": failedNetworkCreate, "final-error": failedNetworkReadbackCommand,
				"changed": failedNetworkReadback, "cancelled": failedNetworkDeadline}
			if scenario != "pass" && (err != stages[scenario] || err.Error() != "network startup rejected") {
				t.Fatal("failure classification changed or leaked backend detail", scenario)
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

func TestNetworkExecutableFailureIsNotLostAtAnyCommand(t *testing.T) {
	before := []byte(`{"nftables":[{"metainfo":{"version":"1.0.9","release_name":"Old Doc Yak #3","json_schema_version":1}}]}`)
	for failAt := 1; failAt <= 4; failAt++ {
		calls := 0
		err := applyNetwork(context.Background(), []byte(`{"schema":"nib.guest-network/v1","egress":[]}`),
			func(context.Context, networkOperation, []byte) ([]byte, error) {
				calls++
				if calls == failAt {
					return nil, failedNetworkExecutable
				}
				if calls == 1 {
					return before, nil
				}
				return nil, nil
			})
		if err != failedNetworkExecutable || calls != failAt {
			t.Fatal("lost executable stage or continued after failure")
		}
	}
	for code := failedNetworkPath; code <= failedNetworkDeadline; code++ {
		if code.serviceExitCode() < 64 || code.serviceExitCode() > 77 || code.Error() != errNetwork.Error() {
			t.Fatal("unsafe fixed status")
		}
	}
}

func TestNetworkConfigReadStatusesAreFiniteAndContentFree(t *testing.T) {
	for status := 0; status <= 255; status++ {
		stage := networkFileFailure(status)
		got := networkConfigReadFailure(stage)
		if status >= 78 && status <= 93 {
			if got != stage || stage.serviceExitCode() != status {
				t.Fatal("lost guarded-read status")
			}
		} else if got != failedNetworkConfigRead {
			t.Fatal("unrecognized status forwarded")
		}
		if got.Error() != "network startup rejected" {
			t.Fatal("non-fixed read diagnostic")
		}
	}
	for _, err := range []error{nil, errors.New("PRIVATE FILE ERROR"), failedNetworkExecutable} {
		if networkConfigReadFailure(err) != failedNetworkConfigRead {
			t.Fatal("arbitrary error promoted to read status")
		}
	}
	if failedFileRoot != 78 || failedFileChanged != 93 {
		t.Fatal("read status protocol changed")
	}
}

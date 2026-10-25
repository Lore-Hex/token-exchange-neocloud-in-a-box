// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestBoundedSequence(t *testing.T) {
	for _, failure := range []string{"", "collection", "appraisal", "token-verification", "claims", "inventory-recheck", "substitution", "chain-substitution", "identity", "cancel", "forward-clock", "cleanup", "child-cleanup"} {
		t.Run(failure, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			cert := certPEM(t, 1)
			other := certPEM(t, 2)
			if failure == "chain-substitution" {
				other = certPEM(t, 1)
			}
			dir := t.TempDir()
			now := time.Now()
			wall := now.Add(time.Minute)
			count, checks := 0, 0
			nonce := ""
			work := ""
			op := operations{now: func() time.Time { return now }}
			op.check = func() error {
				checks++
				if failure == "identity" && checks > 2 {
					return errRejected
				}
				return nil
			}
			op.call = func(ctx context.Context, binary string, args, env []string) ([]byte, bool, error) {
				count++
				if binary != executable || !reflect.DeepEqual(args[:4], []string{"--format", "json", "--log-level", "off"}) {
					t.Fatal("changed executable/options")
				}
				flags := map[string]string{}
				for i := 5; i < len(args); i += 2 {
					flags[args[i]] = args[i+1]
				}
				if count == 1 {
					nonce = flags["--nonce"]
				}
				if len(nonce) != 64 || flags["--nonce"] != nonce {
					t.Fatal("nonce changed")
				}
				if len(env) != 6 || !strings.HasPrefix(env[3], "HOME=") || env[4] != "TMPDIR="+strings.TrimPrefix(env[3], "HOME=") {
					t.Fatal("ambient environment")
				}
				work = strings.TrimPrefix(env[3], "HOME=")
				if info, e := os.Stat(work); e != nil || info.Mode().Perm() != 0o700 {
					t.Fatal("private scratch")
				}
				if _, ok := ctx.Deadline(); !ok {
					t.Fatal("missing original deadline")
				}
				stage := []string{"collection", "appraisal", "token-verification", "inventory-recheck"}[count-1]
				if failure == stage {
					return nil, true, errRejected
				}
				if failure == "child-cleanup" && count == 2 {
					return nil, false, errRejected
				}
				if failure == "cancel" && count == 2 {
					cancel()
				}
				if failure == "forward-clock" && count == 2 {
					now = wall.Add(time.Second)
				}
				m := validClaims(nonce, "1")
				switch count {
				case 1, 4:
					if !reflect.DeepEqual(args[4:], []string{"collect-evidence", "--device", "gpu", "--gpu-evidence-source", "nvml", "--nonce", nonce}) {
						t.Fatal("collection argv")
					}
					if (failure == "substitution" || failure == "chain-substitution") && count == 4 {
						cert = other
					}
					return encoded(t, success(map[string]any{"evidences": []any{evidenceRow(nonce, cert)}})), true, nil
				case 2:
					if args[4] != "attest" || flags["--verifier"] != "remote" || flags["--gpu-evidence-source"] != "file" || flags["--nras-url"] != nras || len(args) != 17 {
						t.Fatal("appraisal argv")
					}
					path := flags["--gpu-evidence-file"]
					info, e := os.Stat(path)
					if e != nil || info.Mode().Perm() != 0o600 {
						t.Fatal("evidence privacy")
					}
					raw, e := os.ReadFile(path)
					if e != nil || !strings.Contains(string(raw), nonce) {
						t.Fatal("evidence join")
					}
					return encoded(t, success(map[string]any{"claims": []any{m}, "detached_eat": syntheticToken(t, m)})), true, nil
				case 3:
					if args[4] != "verify-token" || flags["--token-type"] != "detached-eat" || flags["--nras-url"] != nras || len(args) != 13 {
						t.Fatal("verification argv")
					}
					info, e := os.Stat(flags["--token-file"])
					if e != nil || info.Mode().Perm() != 0o600 {
						t.Fatal("token privacy")
					}
					if failure == "claims" {
						m["secboot"] = false
					}
					if failure == "cleanup" {
						now = wall
					}
					return encoded(t, success(map[string]any{"claims": []any{m}})), true, nil
				}
				t.Fatal("retry/extra invocation")
				return nil, false, errRejected
			}
			r := collect(ctx, wall, dir, op)
			if failure == "" {
				if r.Result != "pass" || !r.AppraisalAccepted || r.DeviceCount != 1 || count != 4 {
					t.Fatalf("result %#v calls %d", r, count)
				}
			} else if r.Result != "fail" || r.AppraisalAccepted {
				t.Fatalf("failure promoted %#v", r)
			}
			if r.CleanupConfirmed != (failure != "child-cleanup") || r.RuntimeReady || r.BootVerified || r.InventoryVerified || r.Confidential || r.Deployable {
				t.Fatal("claim/cleanup boundary", r)
			}
			if work != "" {
				if _, e := os.Stat(work); !os.IsNotExist(e) {
					t.Fatal("private scratch retained")
				}
			}
			out := string(encoded(t, r))
			for _, secret := range []string{canary, nonce, dir, string(cert)} {
				if secret != "" && strings.Contains(out, secret) {
					t.Fatal("private content escaped")
				}
			}
		})
	}
}

func TestDeadlineAndInitialFailureNeverExecute(t *testing.T) {
	for _, duration := range []time.Duration{-time.Second, 0, cleanupReserve, MaxDuration + time.Second} {
		now := time.Now()
		calls := 0
		r := collect(context.Background(), now.Add(duration), t.TempDir(), operations{check: func() error { return nil }, now: func() time.Time { return now }, call: func(context.Context, string, []string, []string) ([]byte, bool, error) {
			calls++
			return nil, true, nil
		}})
		if r.Result != "fail" || calls != 0 {
			t.Fatal("invalid budget admitted")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := collect(ctx, time.Now().Add(time.Minute), t.TempDir(), operations{check: func() error { t.Fatal("check after cancellation"); return nil }, now: time.Now})
	if r.Result != "fail" {
		t.Fatal("cancelled admitted")
	}
}

func TestOriginalMonotonicDeadlineSurvivesBackwardClock(t *testing.T) {
	now := time.Now()
	wall := now.Add(cleanupReserve + 100*time.Millisecond)
	op := operations{check: func() error { return nil }, now: func() time.Time { return now }}
	op.call = func(ctx context.Context, _ string, _, _ []string) ([]byte, bool, error) {
		if deadline, ok := ctx.Deadline(); !ok || deadline.After(wall.Add(-cleanupReserve)) {
			t.Fatal("extended original deadline")
		}
		now = now.Add(-time.Hour)
		select {
		case <-ctx.Done():
			return nil, true, errRejected
		case <-time.After(3 * time.Second):
			t.Fatal("backward clock extended execution")
			return nil, false, errRejected
		}
	}
	r := collect(context.Background(), wall, t.TempDir(), op)
	if r.Result != "fail" || r.AppraisalAccepted || !r.CleanupConfirmed {
		t.Fatal("expired sequence promoted", r)
	}
}

func TestCleanupFailureCannotPass(t *testing.T) {
	// An impossible child of a regular device exercises a real bounded
	// filesystem failure without mutating the device or requiring privilege.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := collectWork(ctx, time.Now().Add(time.Minute), "/dev/null/child", operations{now: time.Now}, func() error { return errRejected }, report())
	if r.Result != "fail" || r.AppraisalAccepted || r.CleanupConfirmed || r.Stage != "cleanup" {
		t.Fatal("unknown scratch cleanup promoted", r)
	}
}

func TestVerifiedTokenFailuresRemainClosed(t *testing.T) {
	m := validClaims("nonce", "1")
	a := success(map[string]any{"claims": []any{m}, "detached_eat": syntheticToken(t, m)})
	b := success(map[string]any{"claims": []any{m}})
	if verified(a, b, "nonce", "1", time.Now()) != nil {
		t.Fatal("valid synthetic fixture")
	}
	for _, token := range []any{nil, []any{}, []any{[]any{"JWT", "x"}, map[string]any{"a": "e30.bm90LWpzb24.x"}}, []any{[]any{"JWT", "x"}, map[string]any{"a": "x", "b": "y"}}} {
		a["detached_eat"] = token
		if verified(a, b, "nonce", "1", time.Now()) == nil {
			t.Fatal("invalid signed token accepted")
		}
	}
	raw := encoded(t, b)
	var altered map[string]any
	if json.Unmarshal(raw, &altered) != nil {
		t.Fatal("fixture")
	}
	altered["claims"].([]any)[0].(map[string]any)["unrecognized-extra"] = true
	a["detached_eat"] = syntheticToken(t, m)
	if verified(a, altered, "nonce", "1", time.Now()) == nil {
		t.Fatal("CLI claims disagreement ignored")
	}
}

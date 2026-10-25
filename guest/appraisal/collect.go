// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"time"
)

const MaxDuration = 285 * time.Second
const cleanupReserve = 5 * time.Second
const nras = "https://nras.attestation.nvidia.com"
const executable = "/usr/bin/nvattest"

// Report is metadata only. Even successful SDK appraisal is not a boot-image,
// assigned-device inventory, readiness, TLS or confidential-service verdict.
type Report struct {
	Schema            string `json:"schema"`
	Result            string `json:"result"`
	Stage             string `json:"stage"`
	SDKSource         string `json:"sdk_source"`
	Deadline          string `json:"deadline"`
	AppraisalAccepted bool   `json:"appraisal_accepted"`
	DeviceCount       int    `json:"device_count"`
	CleanupConfirmed  bool   `json:"cleanup_confirmed"`
	RuntimeReady      bool   `json:"runtime_ready"`
	BootVerified      bool   `json:"boot_verified"`
	InventoryVerified bool   `json:"assigned_inventory_verified"`
	Confidential      bool   `json:"confidential"`
	Deployable        bool   `json:"deployable"`
}

func report() Report {
	return Report{Schema: "nib.h100-appraisal-collector/v1", Result: "fail", Stage: "admission", SDKSource: sdkSource}
}

// Private dependencies make tests deterministic without a production executor,
// endpoint, input-evidence or policy override.
type operations struct {
	check func() error
	call  func(context.Context, string, []string, []string) ([]byte, bool, error)
	now   func() time.Time
}

func collect(ctx context.Context, wall time.Time, directory string, op operations) Report {
	r := report()
	now := op.now()
	hard := now.Add(wall.Sub(now))
	if remaining := hard.Sub(now); remaining <= cleanupReserve || remaining > MaxDuration {
		return r
	}
	r.Deadline = wall.UTC().Format(time.RFC3339)
	ctx, cancel := context.WithDeadline(ctx, hard.Add(-cleanupReserve))
	defer cancel()
	checkpoint := func() error {
		if ctx.Err() != nil || !op.now().Before(hard.Add(-cleanupReserve)) || !op.now().UTC().Before(wall.Add(-cleanupReserve)) {
			return errRejected
		}
		return op.check()
	}
	if checkpoint() != nil {
		return r
	}
	work, err := os.MkdirTemp(directory, "nib-h100-appraisal-")
	if err != nil {
		return r
	}
	// Cleanup must be reflected in the returned value, including early failure.
	return collectWork(ctx, wall, work, op, checkpoint, r)
}

func collectWork(ctx context.Context, wall time.Time, work string, op operations, checkpoint func() error, r Report) (result Report) {
	result = r
	childrenGone := true
	defer func() {
		err := os.RemoveAll(work)
		_, absent := os.Lstat(work)
		result.CleanupConfirmed = childrenGone && err == nil && os.IsNotExist(absent)
		if !result.CleanupConfirmed || ctx.Err() != nil || !op.now().Before(wall) {
			result.Result, result.Stage, result.AppraisalAccepted = "fail", "cleanup", false
		}
	}()
	env := []string{"PATH=/usr/bin:/usr/sbin", "LANG=C", "LC_ALL=C", "HOME=" + work, "TMPDIR=" + work,
		"LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu"}
	call := func(args []string, fields ...string) (map[string]any, error) {
		if checkpoint() != nil {
			return nil, errRejected
		}
		raw, gone, err := op.call(ctx, executable, append([]string{"--format", "json", "--log-level", "off"}, args...), env)
		childrenGone = childrenGone && gone
		if err != nil || !gone || checkpoint() != nil {
			return nil, errRejected
		}
		return output(raw, fields...)
	}
	write := func(name string, value any) (string, error) {
		raw, err := json.Marshal(value)
		if err != nil || len(raw) > limit {
			return "", errRejected
		}
		path := filepath.Join(work, name)
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if err != nil {
			return "", errRejected
		}
		_, err = f.Write(raw)
		closeErr := f.Close()
		if err != nil || closeErr != nil {
			return "", errRejected
		}
		return path, nil
	}
	nonce := make([]byte, 32)
	if _, err := rand.Read(nonce); err != nil {
		return result
	}
	n := hex.EncodeToString(nonce)
	collectDevice := func() ([]byte, device, error) {
		row, err := call([]string{"collect-evidence", "--device", "gpu", "--gpu-evidence-source", "nvml", "--nonce", n}, "evidences")
		if err != nil {
			return nil, device{}, err
		}
		raw, err := json.Marshal(row)
		if err != nil {
			return nil, device{}, errRejected
		}
		return evidence(raw, n)
	}
	result.Stage = "collection"
	raw, local, err := collectDevice()
	if err != nil {
		return result
	}
	var collected any
	if json.Unmarshal(raw, &collected) != nil {
		return result
	}
	file, err := write("evidence.json", collected)
	if err != nil {
		return result
	}
	result.Stage = "appraisal"
	attested, err := call([]string{"attest", "--device", "gpu", "--verifier", "remote", "--gpu-evidence-source", "file",
		"--gpu-evidence-file", file, "--nonce", n, "--nras-url", nras}, "claims", "detached_eat")
	if err != nil {
		return result
	}
	token, err := write("token.json", attested["detached_eat"])
	if err != nil {
		return result
	}
	result.Stage = "token-verification"
	checked, err := call([]string{"verify-token", "--token-type", "detached-eat", "--token-file", token, "--nonce", n, "--nras-url", nras}, "claims")
	if err != nil {
		return result
	}
	result.Stage = "claims"
	if verified(attested, checked, n, local.serial, op.now()) != nil {
		return result
	}
	result.Stage = "inventory-recheck"
	_, after, err := collectDevice()
	if err != nil || !reflect.DeepEqual(local, after) || checkpoint() != nil || verified(attested, checked, n, local.serial, op.now()) != nil {
		return result
	}
	result.Result, result.Stage, result.AppraisalAccepted, result.DeviceCount = "pass", "complete", true, 1
	return result
}

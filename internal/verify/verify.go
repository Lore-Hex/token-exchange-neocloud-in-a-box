// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"encoding/hex"
	"errors"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

var ErrGPUIncomplete = errors.New("GPU cryptographic appraisal is not implemented; confidentiality is unverified")

type Result struct {
	Status           string `json:"status"`
	CPUVerified      bool   `json:"cpu_verified"`
	GPUVerified      bool   `json:"gpu_verified"`
	Confidential     bool   `json:"confidential"`
	FakeChecksPassed bool   `json:"fake_checks_passed"`
	ReleaseID        string `json:"release_id"`
	ProfileID        string `json:"profile_id"`
}

func Check(ctx context.Context, observed Observation, policy Policy) (Result, error) {
	result := Result{Status: "fail"}
	if !policy.valid {
		return result, errors.New("validated acceptance policy is required")
	}
	result.ReleaseID, result.ProfileID = policy.releaseID, policy.profileID
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return result, errors.New("verification cancelled")
	}
	wire := observed.Response
	data := protocol.ReportData(observed.Nonce, observed.SPKI)
	if wire.Protocol != protocol.Name || wire.TEE != "tdx" || wire.CPU == nil || wire.Nonce != hex.EncodeToString(observed.Nonce[:]) || wire.TLSSPKISHA256 != hex.EncodeToString(observed.SPKI[:]) || wire.ReportData != hex.EncodeToString(data[:]) {
		return result, errors.New("attestation transcript binding failed")
	}
	if len(wire.CPU.Evidence) == 0 || len(wire.CPU.Evidence) > 1<<20 || len(wire.CPU.Aux) > 4<<20 {
		return result, errors.New("CPU evidence size is invalid")
	}
	if err := appraiseCPU(ctx, *wire.CPU, policy, data); err != nil {
		return result, err
	}
	result.CPUVerified = !fakeBuild
	if err := checkDevices(wire, policy, protocol.GPUNonce(data)); err != nil {
		return result, err
	}
	if fakeBuild {
		if err := appraiseFakeDevices(wire.GPUEvidence, protocol.GPUNonce(data)); err != nil {
			return result, err
		}
		result.Status, result.FakeChecksPassed = "fake_pass", true
		return result, nil
	}
	result.Status = "partial"
	return result, ErrGPUIncomplete
}

// Structural checks are not GPU signature verification. In P1 a release build
// always returns partial/fail; no collector flag or envelope nonce can bypass it.
func checkDevices(wire protocol.Response, policy Policy, nonce [32]byte) error {
	if wire.GPUError != nil || len(wire.GPUEvidence) != policy.gpus+policy.switches {
		return errors.New("GPU and fabric evidence inventory is incomplete")
	}
	seen := make(map[string]bool)
	gpus, switches, total := 0, 0, 0
	for _, b := range wire.GPUEvidence {
		total += len(b.Evidence)
		if b.Vendor != "nvidia" || b.Format == "" || len(b.Format) > 128 || b.DeviceID == "" || len(b.DeviceID) > 128 || b.Nonce != hex.EncodeToString(nonce[:]) || len(b.Evidence) == 0 || total > 8<<20 || seen[b.DeviceID] {
			return errors.New("invalid or duplicate device evidence")
		}
		seen[b.DeviceID] = true
		switch b.DeviceType {
		case "gpu":
			gpus++
		case "nvswitch":
			switches++
		default:
			return errors.New("unsupported device evidence kind")
		}
	}
	if gpus != policy.gpus || switches != policy.switches {
		return errors.New("device evidence counts do not match the pinned profile")
	}
	return nil
}

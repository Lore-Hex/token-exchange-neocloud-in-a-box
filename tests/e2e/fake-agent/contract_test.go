//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/verify"
)

// This checks the public collector/appraiser contract, not trcs. The full
// container test must still exercise the actual pinned upstream binary.
func TestCollectorVerifierContract(t *testing.T) {
	release, err := os.ReadFile("../../../internal/config/testdata/valid/release.json")
	if err != nil {
		t.Fatal(err)
	}
	profile, err := os.ReadFile("../../../internal/config/testdata/valid/hardware-profile.json")
	if err != nil {
		t.Fatal(err)
	}
	var hardware map[string]any
	if err := json.Unmarshal(profile, &hardware); err != nil {
		t.Fatal(err)
	}
	hardware["pci_devices"] = append(hardware["pci_devices"].([]any), map[string]any{"kind": "nvswitch", "address": "0000:03:00.0"})
	profile, err = json.Marshal(hardware)
	if err != nil {
		t.Fatal(err)
	}
	releaseHash, profileHash := sha256.Sum256(release), sha256.Sum256(profile)
	policy, err := verify.LoadPolicy(release, hex.EncodeToString(releaseHash[:]), profile, hex.EncodeToString(profileHash[:]))
	if err != nil {
		t.Fatal(err)
	}
	path := state(t, "valid")
	aa := httptest.NewServer(nibfakeAA(path))
	defer aa.Close()
	client := aa.Client()
	client.Timeout = time.Second
	for _, mode := range []string{"valid", "cpu-binding", "cpu-nonce", "cpu-measurement", "cpu-rtmr0", "cpu-rtmr1", "cpu-rtmr2",
		"cpu-debug", "cpu-migratable", "cpu-truncated", "gpu-missing", "gpu-duplicate", "gpu-nonce", "gpu-identity", "gpu-kind", "valid"} {
		t.Run(mode, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(mode), 0600); err != nil {
				t.Fatal(err)
			}
			observed := verify.Observation{Nonce: [32]byte{1}, SPKI: [32]byte{2}}
			data := protocol.ReportData(observed.Nonce, observed.SPKI)
			gpu := protocol.GPUNonce(data)
			response, err := client.Get(aa.URL + "/aa/evidence?encoding=base64&runtime_data=" + base64.RawURLEncoding.EncodeToString(data[:]))
			if err != nil {
				t.Fatal("AA transport failed")
			}
			quote, err := io.ReadAll(io.LimitReader(response.Body, 4096))
			response.Body.Close()
			if err != nil || response.StatusCode != 200 || !bytes.HasPrefix(quote, []byte(protocol.FakeTDXMagic)) {
				t.Fatal("AA did not return explicit fake bytes")
			}
			var helper bytes.Buffer
			if err := nibfakeGPU(path, hex.EncodeToString(gpu[:]), &helper); err != nil {
				t.Fatal(err)
			}
			var wire struct {
				Devices []helperDevice `json:"gpu_evidence"`
			}
			if err := json.Unmarshal(helper.Bytes(), &wire); err != nil {
				t.Fatal(err)
			}
			observed.Response = protocol.Response{
				Protocol: protocol.Name, TEE: "tdx", Nonce: hex.EncodeToString(observed.Nonce[:]),
				TLSSPKISHA256: hex.EncodeToString(observed.SPKI[:]), ReportData: hex.EncodeToString(data[:]),
				CPU: &protocol.CPU{Source: "coco-aa", Provider: "coco-aa", Evidence: quote},
			}
			for _, d := range wire.Devices {
				observed.Response.GPUEvidence = append(observed.Response.GPUEvidence, protocol.Device{
					Vendor: d.Vendor, Format: d.Format, Nonce: hex.EncodeToString(gpu[:]),
					Evidence: d.Evidence, DeviceType: d.DeviceType, DeviceID: d.DeviceID,
				})
			}
			result, err := verify.Check(context.Background(), observed, policy)
			valid := mode == "valid"
			if (err == nil) != valid || result.FakeChecksPassed != valid || result.CPUVerified || result.GPUVerified || result.Confidential {
				t.Fatal("collector/appraiser contract or fake-only claims failed")
			}
			wantStatus := "fail"
			if valid {
				wantStatus = "fake_pass"
			}
			if result.Status != wantStatus {
				t.Fatal("incorrect fake result status")
			}
		})
	}
}

// SPDX-License-Identifier: Apache-2.0

// Package protocol implements the public tr-attestation/v0 wire contract.
// It contains no verifier or claims of hardware authenticity.
package protocol

import "crypto/sha256"

const Name = "tr-attestation/v0"
const Path = "/.well-known/trustedrouter/attestation"

type CPU struct {
	Source   string `json:"source"`
	Provider string `json:"provider"`
	Evidence []byte `json:"evidence"`
	Aux      []byte `json:"aux"`
}

type Device struct {
	Vendor     string `json:"vendor"`
	Format     string `json:"format"`
	Nonce      string `json:"nonce"`
	Evidence   []byte `json:"evidence"`
	DeviceType string `json:"device_type,omitempty"`
	DeviceID   string `json:"device_id,omitempty"`
}

type Workload struct {
	StackVersion  string `json:"stack_version"`
	Model         string `json:"model"`
	ModelRevision string `json:"model_revision"`
	EngineImage   string `json:"engine_image"`
}

type Response struct {
	Protocol      string   `json:"protocol"`
	TEE           string   `json:"tee"`
	Nonce         string   `json:"nonce"`
	TLSSPKISHA256 string   `json:"tls_spki_sha256"`
	ReportData    string   `json:"report_data"`
	CPU           *CPU     `json:"cpu"`
	GPUEvidence   []Device `json:"gpu_evidence"`
	GPUError      *string  `json:"gpu_error"`
	Workload      Workload `json:"workload"`
	Confidential  bool     `json:"confidential"`
}

func ReportData(nonce, spki [32]byte) [64]byte {
	var data [64]byte
	copy(data[:32], spki[:])
	h := sha256.New()
	h.Write([]byte(Name + "\x00"))
	h.Write(nonce[:])
	copy(data[32:], h.Sum(nil))
	return data
}

func GPUNonce(data [64]byte) [32]byte {
	h := sha256.New()
	h.Write([]byte(Name + "/gpu\x00"))
	h.Write(data[:])
	var nonce [32]byte
	copy(nonce[:], h.Sum(nil))
	return nonce
}

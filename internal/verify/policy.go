// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"go.yaml.in/yaml/v3"
)

// Policy can only be populated by validating independently pinned local files.
// A remote attestation response must never supply its own acceptance policy.
type Policy struct {
	releaseID    string
	profileID    string
	measurements [4][48]byte // MRTD, RTMR0, RTMR1, RTMR2; RTMR3 is reserved.
	gpus         int
	switches     int
	valid        bool
}

func pinned(data []byte, expected string) bool {
	hash := sha256.Sum256(data)
	return len(expected) == 64 && hex.EncodeToString(hash[:]) == expected
}

func LoadPolicy(release []byte, releaseSHA256 string, profile []byte, profileSHA256 string) (Policy, error) {
	var p Policy
	if !pinned(release, releaseSHA256) || !pinned(profile, profileSHA256) {
		return p, errors.New("policy file digest mismatch")
	}
	if config.ValidateRelease(release) != nil || config.ValidateHardwareProfile(profile) != nil {
		return p, errors.New("invalid release or hardware profile")
	}
	var manifest struct {
		ID       string `json:"id"`
		Profiles map[string]struct {
			MRTD  string `json:"mrtd"`
			RTMR0 string `json:"rtmr0"`
			RTMR1 string `json:"rtmr1"`
			RTMR2 string `json:"rtmr2"`
		} `json:"profiles"`
	}
	var hardware struct {
		ID     string `yaml:"id"`
		CCMode string `yaml:"cc_mode"`
		GPU    struct {
			Count int `yaml:"count"`
		} `yaml:"gpu"`
		Devices []struct {
			Kind string `yaml:"kind"`
		} `yaml:"pci_devices"`
	}
	if json.Unmarshal(release, &manifest) != nil || yaml.Unmarshal(profile, &hardware) != nil {
		return p, errors.New("cannot decode acceptance policy")
	}
	if hardware.CCMode == "none" {
		return p, errors.New("non-confidential hardware profile is not acceptable")
	}
	m, ok := manifest.Profiles[hardware.ID]
	if !ok {
		return p, errors.New("release does not include the hardware profile")
	}
	for i, value := range []string{m.MRTD, m.RTMR0, m.RTMR1, m.RTMR2} {
		decoded, err := hex.DecodeString(value)
		if err != nil || len(decoded) != 48 {
			return Policy{}, errors.New("invalid expected measurement")
		}
		copy(p.measurements[i][:], decoded)
	}
	for _, device := range hardware.Devices {
		if device.Kind == "nvswitch" {
			p.switches++
		}
	}
	if p.switches+hardware.GPU.Count > 64 {
		return Policy{}, errors.New("hardware inventory exceeds supported evidence count")
	}
	p.gpus, p.releaseID, p.profileID, p.valid = hardware.GPU.Count, manifest.ID, hardware.ID, true
	return p, nil
}

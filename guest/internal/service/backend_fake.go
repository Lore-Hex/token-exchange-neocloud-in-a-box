//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

func runBackend(ctx context.Context, role, path string, out io.Writer) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	s, err := openFakeConfig(path)
	if err != nil {
		return err
	}
	defer s.root.Close()
	switch role {
	case "gpu-gate":
		err = nibfakeGPU(s)
	case "weights":
		err = nibfakeWeights(s)
	case "network":
		err = nibfakeNetwork(s)
	case "engine":
		return nibfakeEngine(ctx, s, out)
	}
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(out, "NIBFAKE: simulated gate passed; no hardware, mount, firewall or confidentiality validation")
	return err
}

//go:noinline
func nibfakeGPU(s *fakeSession) error {
	var inventory struct {
		Schema  string `json:"schema"`
		Devices []struct {
			ID       string `json:"id"`
			Kind     string `json:"kind"`
			Mode     string `json:"mode"`
			Approved bool   `json:"approved"`
			Debug    *bool  `json:"debug"`
		} `json:"devices"`
	}
	b, err := readFakeFile(s.root, s.config.InventoryFile, configLimit)
	if err != nil || strictJSON(b, &inventory) != nil || inventory.Schema != "nib.fake-inventory/v1" || len(inventory.Devices) != len(s.config.Devices) {
		return errors.New("simulated inventory rejected")
	}
	expected := make(map[string]string)
	for _, d := range s.config.Devices {
		expected[d.ID] = d.Kind
	}
	for _, d := range inventory.Devices {
		if expected[d.ID] != d.Kind || d.Kind == "" || d.Mode != "fake-cc" || !d.Approved || d.Debug == nil || *d.Debug {
			return errors.New("simulated device rejected")
		}
		delete(expected, d.ID)
	}
	if len(expected) != 0 {
		return errors.New("simulated device missing")
	}
	return nil
}

//go:noinline
func nibfakeWeights(s *fakeSession) error {
	for _, w := range s.config.Weights {
		b, err := readFakeFile(s.root, w.Path, w.Bytes)
		if err != nil || int64(len(b)) != w.Bytes {
			return errors.New("simulated weight read failed")
		}
		h := sha256.Sum256(b)
		if hex.EncodeToString(h[:]) != w.SHA256 {
			return errors.New("simulated weight integrity failed")
		}
	}
	return nil
}

//go:noinline
func nibfakeNetwork(s *fakeSession) error {
	policy, err := nftables.Render(s.config.Egress)
	if err != nil {
		return err
	}
	var state struct {
		Schema       string `json:"schema"`
		PolicySHA256 string `json:"policy_sha256"`
		Applied      bool   `json:"applied"`
	}
	b, err := readFakeFile(s.root, s.config.NetworkFile, configLimit)
	if err != nil || strictJSON(b, &state) != nil || state.Schema != "nib.fake-network/v1" || !state.Applied || state.PolicySHA256 != fmt.Sprintf("%x", sha256.Sum256(policy)) {
		return errors.New("simulated network policy rejected")
	}
	return nil
}

func (s *fakeSession) checkGates() error {
	for _, gate := range []func(*fakeSession) error{nibfakeNetwork, nibfakeGPU, nibfakeWeights} {
		if err := gate(s); err != nil {
			return err
		}
	}
	return nil
}

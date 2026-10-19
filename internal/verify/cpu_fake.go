//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"bytes"
	"context"
	"errors"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

const fakeBuild = true

func appraiseCPU(ctx context.Context, cpu protocol.CPU, p Policy, data [64]byte) error {
	return nibfakeAppraiseCPU(ctx, cpu, p, data)
}

//go:noinline
func nibfakeAppraiseCPU(ctx context.Context, cpu protocol.CPU, p Policy, data [64]byte) error {
	if ctx.Err() != nil {
		return errors.New("fake verification cancelled")
	}
	if cpu.Source != "nibfake" || cpu.Provider != "nibfake-tdx-v1" || !bytes.Equal(cpu.Evidence, protocol.FakeTDXQuote(data, p.measurements, 0)) {
		return errors.New("fake CPU transcript, measurements or attributes do not match")
	}
	return nil
}

func appraiseFakeDevices(devices []protocol.Device, nonce [32]byte) error {
	return nibfakeAppraiseDevices(devices, nonce)
}

//go:noinline
func nibfakeAppraiseDevices(devices []protocol.Device, nonce [32]byte) error {
	for _, d := range devices {
		if d.Format != protocol.FakeDeviceFormat || !bytes.Equal(d.Evidence, protocol.FakeDeviceEvidence(nonce, d.DeviceType, d.DeviceID)) {
			return errors.New("fake device evidence does not match its nonce and identity")
		}
	}
	return nil
}

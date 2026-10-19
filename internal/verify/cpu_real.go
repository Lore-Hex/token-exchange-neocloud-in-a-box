//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"errors"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
	"github.com/google/go-tdx-guest/abi"
	"github.com/google/go-tdx-guest/validate"
	tdx "github.com/google/go-tdx-guest/verify"
)

const fakeBuild = false

func cpuPolicy(p Policy, data [64]byte) *validate.Options {
	return &validate.Options{TdQuoteBodyOptions: validate.TdQuoteBodyOptions{
		MrTd:       p.measurements[0][:],
		Rtmrs:      [][]byte{p.measurements[1][:], p.measurements[2][:], p.measurements[3][:], nil},
		ReportData: data[:], EnableTdDebugCheck: true, EnableTdMigratableCheck: true,
	}}
}

func cpuOptions() *tdx.Options {
	opts := tdx.DefaultOptions()
	opts.GetCollateral = true
	opts.CheckRevocations = true
	opts.DisableTcbStatusCheck = false
	opts.Getter = newCollateralGetter()
	return opts
}

func appraiseCPU(ctx context.Context, cpu protocol.CPU, policy Policy, data [64]byte) error {
	if cpu.Source != "configfs-tsm" || cpu.Provider != "tdx_guest" {
		return errors.New("unsupported CPU evidence source; raw TDX quote required")
	}
	quote, err := abi.QuoteToProto(cpu.Evidence)
	if err != nil {
		return errors.New("invalid TDX quote")
	}
	// Reject mismatched policy before spending resources retrieving collateral.
	// These checks become authenticated only after signature verification.
	if err := validate.TdxQuote(quote, cpuPolicy(policy, data)); err != nil {
		return errors.New("TDX quote does not satisfy measurement or attribute policy")
	}
	if err := tdx.TdxQuoteContext(ctx, quote, cpuOptions()); err != nil {
		return errors.New("TDX signature, collateral, revocation or TCB verification failed")
	}
	return nil
}

func appraiseFakeDevices([]protocol.Device, [32]byte) error {
	return errors.New("fake evidence is not supported by release builds")
}

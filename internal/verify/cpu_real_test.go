//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
	"github.com/google/go-tdx-guest/verify/trust"
)

func TestTDXFailClosedOptions(t *testing.T) {
	opts := cpuOptions()
	if !opts.CheckRevocations || !opts.GetCollateral || opts.DisableTcbStatusCheck || opts.TrustedRoots != nil {
		t.Fatal("unsafe Intel verification defaults")
	}
	if _, ok := opts.Getter.(trust.ContextHTTPSGetter); !ok {
		t.Fatal("collateral retrieval ignores context")
	}
	p := testPolicy(t)
	data := [64]byte{1}
	policy := cpuPolicy(p, data).TdQuoteBodyOptions
	if !policy.EnableTdDebugCheck || !policy.EnableTdMigratableCheck || len(policy.MrTd) != 48 || len(policy.ReportData) != 64 || len(policy.Rtmrs) != 4 {
		t.Fatal("unsafe measurement or attribute policy")
	}
	for i := 0; i < 3; i++ {
		if len(policy.Rtmrs[i]) != 48 {
			t.Fatal("missing RTMR policy")
		}
	}
	if policy.Rtmrs[3] != nil {
		t.Fatal("reserved RTMR3 was silently assigned a value")
	}
}

func TestReleaseRejectsFakeAndMalformedCPU(t *testing.T) {
	p := testPolicy(t)
	for _, cpu := range []protocol.CPU{
		{Source: "nibfake", Provider: "nibfake-tdx-v1", Evidence: []byte("NIBFAKE-TDX-v1\n")},
		{Source: "configfs-tsm", Provider: "tdx_guest", Evidence: []byte("NIBFAKE-TDX-v1\n")},
		{Source: "configfs-tsm", Provider: "tdx_guest", Evidence: []byte{4, 0}},
		{Source: "coco-aa", Provider: "tdx_guest", Evidence: []byte("unparsed-agent-envelope")},
	} {
		if err := appraiseCPU(context.Background(), cpu, p, [64]byte{}); err == nil {
			t.Fatal("release accepted fake or malformed CPU evidence")
		}
	}
	if err := appraiseFakeDevices(nil, [32]byte{}); err == nil || fakeBuild {
		t.Fatal("release can accept fake GPU appraisal")
	}
}

//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"bytes"
	"context"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
	"github.com/google/go-tdx-guest/abi"
	pb "github.com/google/go-tdx-guest/proto/tdx"
	"github.com/google/go-tdx-guest/testing/testdata"
	"github.com/google/go-tdx-guest/validate"
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

// These upstream public fixtures exercise field-policy validation on real wire
// layouts only. Modified quotes do not have valid signatures and are never
// treated as a positive hardware verification result.
func TestRealQuoteLayoutsEnforcePolicyMutations(t *testing.T) {
	p := testPolicy(t)
	data := protocol.ReportData([32]byte{1}, [32]byte{2})
	for name, raw := range map[string][]byte{"v4": testdata.RawQuote, "v5": testdata.RawQuoteV5} {
		t.Run(name, func(t *testing.T) {
			for _, mutation := range []string{"none", "mrtd", "rtmr0", "rtmr1", "rtmr2", "report_data", "debug", "migratable"} {
				t.Run(mutation, func(t *testing.T) {
					quote, err := abi.QuoteToProto(bytes.Clone(raw))
					if err != nil {
						t.Fatal(err)
					}
					var fields map[string][]byte
					switch q := quote.(type) {
					case *pb.QuoteV4:
						body := q.TdQuoteBody
						fields = map[string][]byte{"mrtd": body.MrTd, "rtmr0": body.Rtmrs[0], "rtmr1": body.Rtmrs[1], "rtmr2": body.Rtmrs[2], "report_data": body.ReportData, "attributes": body.TdAttributes}
					case *pb.QuoteV5:
						body := q.TdQuoteBodyDescriptor.TdQuoteBodyV5
						fields = map[string][]byte{"mrtd": body.MrTd, "rtmr0": body.Rtmrs[0], "rtmr1": body.Rtmrs[1], "rtmr2": body.Rtmrs[2], "report_data": body.ReportData, "attributes": body.TdAttributes}
					default:
						t.Fatal("unexpected fixture format")
					}
					for i, field := range []string{"mrtd", "rtmr0", "rtmr1", "rtmr2"} {
						copy(fields[field], p.measurements[i][:])
					}
					copy(fields["report_data"], data[:])
					fields["attributes"][0] &^= 1
					fields["attributes"][3] &^= 0x20
					switch mutation {
					case "none":
					case "debug":
						fields["attributes"][0] |= 1
					case "migratable":
						fields["attributes"][3] |= 0x20
					default:
						fields[mutation][0] ^= 1
					}
					err = validate.TdxQuote(quote, cpuPolicy(p, data))
					if (err == nil) != (mutation == "none") {
						t.Fatalf("policy mutation %s: %v", mutation, err)
					}
				})
			}
		})
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

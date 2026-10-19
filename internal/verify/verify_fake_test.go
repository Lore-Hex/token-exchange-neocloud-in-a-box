//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

func fakeObservation(p Policy) Observation {
	o := Observation{Nonce: [32]byte{1}, SPKI: [32]byte{2}}
	data := protocol.ReportData(o.Nonce, o.SPKI)
	gpu := protocol.GPUNonce(data)
	o.Response = protocol.Response{Protocol: protocol.Name, TEE: "tdx", Nonce: hex.EncodeToString(o.Nonce[:]), TLSSPKISHA256: hex.EncodeToString(o.SPKI[:]), ReportData: hex.EncodeToString(data[:]), CPU: &protocol.CPU{Source: "nibfake", Provider: "nibfake-tdx-v1", Evidence: protocol.FakeTDXQuote(data, p.measurements, 0)}}
	for i := 0; i < p.gpus; i++ {
		id := fmt.Sprintf("GPU-%d", i)
		o.Response.GPUEvidence = append(o.Response.GPUEvidence, protocol.Device{Vendor: "nvidia", Format: protocol.FakeDeviceFormat, Nonce: hex.EncodeToString(gpu[:]), DeviceType: "gpu", DeviceID: id, Evidence: protocol.FakeDeviceEvidence(gpu, "gpu", id)})
	}
	return o
}

func TestFakeChecksNeverClaimHardwareConfidentiality(t *testing.T) {
	p := testPolicy(t)
	o := fakeObservation(p)
	// Deliberately malicious hints must not grant stronger status.
	o.Response.Confidential = true
	o.Response.Workload.Model = "provider-claimed-model"
	r, err := Check(context.Background(), o, p)
	if err != nil || r.Status != "fake_pass" || !r.FakeChecksPassed || r.Confidential || r.CPUVerified || r.GPUVerified {
		t.Fatalf("result=%+v err=%v", r, err)
	}
}

func TestAttestationMutations(t *testing.T) {
	p := testPolicy(t)
	mutations := map[string]func(*Observation){
		"wrong-spki":          func(o *Observation) { o.SPKI[0] ^= 1 },
		"stale-nonce":         func(o *Observation) { o.Nonce[0] ^= 1 },
		"wrong-envelope-spki": func(o *Observation) { o.Response.TLSSPKISHA256 = "wrong" },
		"wrong-report-data":   func(o *Observation) { o.Response.ReportData = "wrong" },
		"wrong-protocol":      func(o *Observation) { o.Response.Protocol = "wrong" },
		"wrong-tee":           func(o *Observation) { o.Response.TEE = "none" },
		"missing-cpu":         func(o *Observation) { o.Response.CPU = nil },
		"wrong-source":        func(o *Observation) { o.Response.CPU.Source = "configfs-tsm" },
		"wrong-provider":      func(o *Observation) { o.Response.CPU.Provider = "tdx_guest" },
		"quote-truncated":     func(o *Observation) { o.Response.CPU.Evidence = o.Response.CPU.Evidence[:20] },
		"quote-extended":      func(o *Observation) { o.Response.CPU.Evidence = append(o.Response.CPU.Evidence, 0) },
		"quote-report-data":   func(o *Observation) { o.Response.CPU.Evidence[len(protocol.FakeTDXMagic)] ^= 1 },
		"debug":               func(o *Observation) { o.Response.CPU.Evidence[len(o.Response.CPU.Evidence)-1] = 1 },
		"migratable":          func(o *Observation) { o.Response.CPU.Evidence[len(o.Response.CPU.Evidence)-1] = 2 },
		"gpu-envelope-nonce":  func(o *Observation) { o.Response.GPUEvidence[0].Nonce = "wrong" },
		"gpu-evidence-nonce":  func(o *Observation) { o.Response.GPUEvidence[0].Evidence[20] ^= 1 },
		"gpu-format":          func(o *Observation) { o.Response.GPUEvidence[0].Format = "unrecognized" },
		"gpu-missing":         func(o *Observation) { o.Response.GPUEvidence = o.Response.GPUEvidence[:1] },
		"gpu-duplicate":       func(o *Observation) { o.Response.GPUEvidence[1] = o.Response.GPUEvidence[0] },
		"gpu-aggregate":       func(o *Observation) { o.Response.GPUEvidence[0].DeviceType = "" },
		"gpu-wrong-kind":      func(o *Observation) { o.Response.GPUEvidence[0].DeviceType = "nvswitch" },
		"gpu-error":           func(o *Observation) { e := "secret-vendor-error"; o.Response.GPUError = &e },
	}
	for i, name := range []string{"mrtd", "rtmr0", "rtmr1", "rtmr2"} {
		mutations[name] = func(o *Observation) { o.Response.CPU.Evidence[len(protocol.FakeTDXMagic)+64+i*48] ^= 1 }
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			o := fakeObservation(p)
			mutate(&o)
			r, err := Check(context.Background(), o, p)
			if err == nil || r.Confidential || r.FakeChecksPassed || r.Status != "fail" {
				t.Fatalf("mutation accepted: %+v %v", r, err)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Check(ctx, fakeObservation(p), p); err == nil {
		t.Fatal("cancelled verification passed")
	}
}

func TestFakeFetchAndVerify(t *testing.T) {
	p := testPolicy(t)
	var spki [32]byte
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		o := fakeObservation(p)
		nonce, err := hex.DecodeString(r.URL.Query().Get("nonce"))
		if err != nil || len(nonce) != 32 {
			t.Error("invalid nonce")
			w.WriteHeader(400)
			return
		}
		copy(o.Nonce[:], nonce)
		o.SPKI = spki
		data := protocol.ReportData(o.Nonce, spki)
		gpu := protocol.GPUNonce(data)
		o.Response.Nonce = hex.EncodeToString(o.Nonce[:])
		o.Response.TLSSPKISHA256 = hex.EncodeToString(spki[:])
		o.Response.ReportData = hex.EncodeToString(data[:])
		o.Response.CPU.Evidence = protocol.FakeTDXQuote(data, p.measurements, 0)
		for i := range o.Response.GPUEvidence {
			d := &o.Response.GPUEvidence[i]
			d.Nonce = hex.EncodeToString(gpu[:])
			d.Evidence = protocol.FakeDeviceEvidence(gpu, d.DeviceType, d.DeviceID)
		}
		_ = json.NewEncoder(w).Encode(o.Response)
	}))
	server.StartTLS()
	defer server.Close()
	spki = sha256.Sum256(server.Certificate().RawSubjectPublicKeyInfo)
	o, err := Fetch(context.Background(), server.URL)
	if err != nil {
		t.Fatal(err)
	}
	r, err := Check(context.Background(), o, p)
	if err != nil || r.Status != "fake_pass" || !r.FakeChecksPassed || r.CPUVerified || r.GPUVerified || r.Confidential {
		t.Fatalf("untruthful fake result: %+v %v", r, err)
	}
}

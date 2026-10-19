//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"syscall"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

func run(ctx context.Context, args []string, out io.Writer) error {
	if len(args) == 3 && args[0] == "gpu" && args[1] == "--state" {
		return nibfakeGPU(args[2], os.Getenv("TRCS_GPU_NONCE"), out)
	}
	if len(args) != 5 || args[0] != "aa" || args[1] != "--listen" || args[3] != "--state" {
		return errors.New("invalid collector invocation")
	}
	a, err := netip.ParseAddrPort(args[2])
	if err != nil || a.Addr().String() != "127.0.0.1" || a.Port() < 1024 {
		return errors.New("collector requires literal loopback")
	}
	l, err := net.Listen("tcp4", a.String())
	if err != nil {
		return errors.New("collector listener unavailable")
	}
	defer l.Close()
	s := &http.Server{Handler: nibfakeAA(args[4]), ReadHeaderTimeout: time.Second,
		ReadTimeout: time.Second, WriteTimeout: time.Second, IdleTimeout: time.Second,
		MaxHeaderBytes: 8192, ErrorLog: log.New(io.Discard, "", 0)}
	done := make(chan error, 1)
	go func() { done <- s.Serve(l) }()
	select {
	case <-ctx.Done():
		s.Close()
		<-done
		return nil
	case <-done:
		return errors.New("collector stopped")
	}
}

func mutation(path string) (string, error) {
	// Nonblocking open prevents a substituted FIFO from hanging before Stat;
	// no-follow rejects a final-component symlink even if it races a path check.
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return "", errors.New("fixture state unavailable")
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 64 {
		return "", errors.New("invalid fixture state")
	}
	b, err := io.ReadAll(io.LimitReader(f, 65))
	if err != nil || len(b) > 64 {
		return "", errors.New("invalid fixture state")
	}
	switch string(b) {
	case "valid", "cpu-binding", "cpu-measurement", "cpu-debug", "cpu-truncated",
		"gpu-missing", "gpu-duplicate", "gpu-nonce", "gpu-identity", "gpu-kind":
		return string(b), nil
	default:
		return "", errors.New("unknown fixture state")
	}
}

// This is the public CoCo AA byte transport, not an Intel quote emulator. The
// magic and measurements are shared with the explicitly tagged fake appraiser.
//
//go:noinline
func nibfakeAA(state string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.Method != "GET" || r.URL.Path != "/aa/evidence" || len(r.URL.RawQuery) > 256 {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		q, err := url.ParseQuery(r.URL.RawQuery)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		b, err := base64.RawURLEncoding.Strict().DecodeString(q.Get("runtime_data"))
		if err != nil || len(b) != 64 || len(q) != 2 || len(q["runtime_data"]) != 1 || len(q["encoding"]) != 1 || q.Get("encoding") != "base64" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		mode, err := mutation(state)
		if err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		var data [64]byte
		copy(data[:], b)
		var measurements [4][48]byte
		for i := range measurements {
			for j := range measurements[i] {
				measurements[i][j] = 0xcc
			}
		}
		var flags byte
		switch mode {
		case "cpu-binding":
			data[0] ^= 1
		case "cpu-measurement":
			measurements[0][0] ^= 1
		case "cpu-debug":
			flags = 1
		}
		quote := protocol.FakeTDXQuote(data, measurements, flags)
		if mode == "cpu-truncated" {
			quote = quote[:len(quote)-1]
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Write(quote)
	})
}

type helperDevice struct {
	Vendor     string `json:"vendor"`
	Format     string `json:"format"`
	Evidence   []byte `json:"evidence_b64"`
	DeviceType string `json:"device_type"`
	DeviceID   string `json:"device_id"`
}

//go:noinline
func nibfakeGPU(state, value string, out io.Writer) error {
	b, err := hex.DecodeString(value)
	if err != nil || len(b) != 32 || strings.ToLower(value) != value {
		return errors.New("invalid GPU nonce")
	}
	mode, err := mutation(state)
	if err != nil {
		return err
	}
	var nonce [32]byte
	copy(nonce[:], b)
	if mode == "gpu-nonce" {
		nonce[0] ^= 1
	}
	devices := []helperDevice{
		{Vendor: "nvidia", DeviceType: "gpu", DeviceID: "gpu0"},
		{Vendor: "nvidia", DeviceType: "gpu", DeviceID: "gpu1"},
		{Vendor: "nvidia", DeviceType: "nvswitch", DeviceID: "switch0"},
	}
	for i := range devices {
		d := &devices[i]
		d.Format = protocol.FakeDeviceFormat
		d.Evidence = protocol.FakeDeviceEvidence(nonce, d.DeviceType, d.DeviceID)
	}
	switch mode {
	case "gpu-missing":
		devices = devices[:2]
	case "gpu-duplicate":
		devices[1] = devices[0]
	case "gpu-identity":
		devices[0].DeviceID = "different"
	case "gpu-kind":
		devices[2].DeviceType = "gpu"
	}
	return json.NewEncoder(out).Encode(struct {
		Devices []helperDevice `json:"gpu_evidence"`
	}{devices})
}

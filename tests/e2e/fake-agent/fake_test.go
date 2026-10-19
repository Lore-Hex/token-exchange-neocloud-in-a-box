//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/protocol"
)

func state(t *testing.T, mode string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "state")
	if err := os.WriteFile(p, []byte(mode), 0600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestAA(t *testing.T) {
	var data [64]byte
	data[0] = 17
	path := "/aa/evidence?encoding=base64&runtime_data=" + base64.RawURLEncoding.EncodeToString(data[:])
	var measurements [4][48]byte
	for i := range measurements {
		for j := range measurements[i] {
			measurements[i][j] = 0xcc
		}
	}
	want := protocol.FakeTDXQuote(data, measurements, 0)
	for _, mode := range []string{"valid", "cpu-binding", "cpu-nonce", "cpu-measurement", "cpu-rtmr0", "cpu-rtmr1", "cpu-rtmr2", "cpu-debug", "cpu-migratable", "cpu-truncated"} {
		t.Run(mode, func(t *testing.T) {
			w := httptest.NewRecorder()
			nibfakeAA(state(t, mode)).ServeHTTP(w, httptest.NewRequest("GET", path, nil))
			if w.Code != 200 || bytes.Equal(w.Body.Bytes(), want) != (mode == "valid") || !bytes.HasPrefix(w.Body.Bytes(), []byte(protocol.FakeTDXMagic)) {
				t.Fatal("unexpected fake quote or mutation")
			}
		})
	}
	for _, target := range []string{"/aa/evidence", path + "&encoding=base64", path + "&extra=x", path + "&invalid=%ZZ", "/private", strings.Replace(path, "runtime_data=", "runtime_data=invalid", 1)} {
		w := httptest.NewRecorder()
		nibfakeAA(state(t, "valid")).ServeHTTP(w, httptest.NewRequest("GET", target, nil))
		if w.Code < 400 || w.Body.Len() != 0 {
			t.Fatal("invalid request accepted or echoed")
		}
	}
	if run(context.Background(), []string{"aa", "--listen", "0.0.0.0:18001", "--state", state(t, "valid")}, &bytes.Buffer{}) == nil {
		t.Fatal("nonloopback listener accepted")
	}
}

func TestGPU(t *testing.T) {
	for _, mode := range []string{"valid", "gpu-missing", "gpu-duplicate", "gpu-nonce", "gpu-identity", "gpu-kind"} {
		var out bytes.Buffer
		if err := nibfakeGPU(state(t, mode), strings.Repeat("00", 32), &out); err != nil {
			t.Fatal(err)
		}
		var decoded struct {
			Devices []helperDevice `json:"gpu_evidence"`
		}
		if err := json.Unmarshal(out.Bytes(), &decoded); err != nil {
			t.Fatal(err)
		}
		if mode == "valid" {
			if len(decoded.Devices) != 3 || decoded.Devices[2].DeviceType != "nvswitch" {
				t.Fatal("incomplete device tags")
			}
			for _, d := range decoded.Devices {
				if !bytes.Equal(d.Evidence, protocol.FakeDeviceEvidence([32]byte{}, d.DeviceType, d.DeviceID)) {
					t.Fatal("invalid device transcript")
				}
			}
		}
	}
	for _, nonce := range []string{"", strings.Repeat("AA", 32), strings.Repeat("0", 63), strings.Repeat("gg", 32)} {
		var out bytes.Buffer
		if nibfakeGPU(state(t, "valid"), nonce, &out) == nil || out.Len() != 0 {
			t.Fatal("invalid nonce accepted")
		}
	}
	for _, mode := range []string{"unknown", strings.Repeat("x", 65)} {
		if _, err := mutation(state(t, mode)); err == nil {
			t.Fatal("invalid state accepted")
		}
	}
}

func TestStateRejectsSpecialFiles(t *testing.T) {
	fifo := filepath.Join(t.TempDir(), "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(state(t, "valid"), link); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{fifo, link, t.TempDir()} {
		done := make(chan error, 1)
		go func() { _, err := mutation(path); done <- err }()
		select {
		case err := <-done:
			if err == nil {
				t.Fatal("special file accepted")
			}
		case <-time.After(time.Second):
			t.Fatal("special file blocked fixture read")
		}
	}
}

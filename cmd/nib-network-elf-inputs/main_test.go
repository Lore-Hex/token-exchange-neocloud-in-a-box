// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"crypto/sha256"
	"debug/elf"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"testing/iotest"
	"time"
)

// Synthetic metadata, not a runnable ELF or a runtime-closure fixture.
func fixture(p pin) []byte {
	raw := make([]byte, 1280)
	copy(raw, []byte("\x7fELF\x02\x01\x01"))
	u16 := func(at int, v uint16) { binary.LittleEndian.PutUint16(raw[at:], v) }
	u32 := func(at int, v uint32) { binary.LittleEndian.PutUint32(raw[at:], v) }
	u64 := func(at int, v uint64) { binary.LittleEndian.PutUint64(raw[at:], v) }
	u16(16, uint16(elf.ET_DYN))
	u16(18, uint16(elf.EM_X86_64))
	u32(20, 1)
	u64(32, 64)
	u64(40, 1024)
	u16(52, 64)
	u16(54, 56)
	u16(56, 3)
	u16(58, 64)
	u16(60, 3)
	interp := []byte("/lib64/ld-linux-x86-64.so.2\x00")
	copy(raw[384:], interp)
	strtab := []byte{0}
	type entry struct{ tag, offset uint64 }
	entries := []entry{}
	add := func(tag elf.DynTag, value string) {
		entries = append(entries, entry{uint64(tag), uint64(len(strtab))})
		strtab = append(strtab, []byte(value+"\x00")...)
	}
	for _, n := range p.Needed {
		add(elf.DT_NEEDED, n)
	}
	for _, n := range p.Runpath {
		add(elf.DT_RUNPATH, n)
	}
	copy(raw[448:], strtab)
	entries = append(entries, entry{uint64(elf.DT_STRTAB), 448}, entry{uint64(elf.DT_STRSZ), uint64(len(strtab))}, entry{})
	for i, e := range entries {
		u64(832+i*16, e.tag)
		u64(840+i*16, e.offset)
	}
	size := uint64(len(entries) * 16)
	for i, p := range []struct {
		kind         uint32
		offset, size uint64
	}{
		{uint32(elf.PT_LOAD), 0, uint64(len(raw))},
		{uint32(elf.PT_INTERP), 384, uint64(len(interp))},
		{uint32(elf.PT_DYNAMIC), 832, size},
	} {
		at := 64 + i*56
		u32(at, p.kind)
		u64(at+8, p.offset)
		u64(at+16, p.offset)
		u64(at+32, p.size)
		u64(at+40, p.size)
	}
	u32(1088+4, uint32(elf.SHT_DYNAMIC))
	u64(1088+16, 832)
	u64(1088+24, 832)
	u64(1088+32, size)
	u32(1088+40, 2)
	u64(1088+56, 16)
	u32(1152+4, uint32(elf.SHT_STRTAB))
	u64(1152+24, 448)
	u64(1152+32, uint64(len(strtab)))
	return raw
}

func syntheticPin(t *testing.T, name string, raw []byte) {
	t.Helper()
	original := pins[name]
	p := original
	p.Bytes = len(raw)
	sha := sha256.Sum256(raw)
	p.SHA256 = hex.EncodeToString(sha[:])
	pins[name] = p
	t.Cleanup(func() { pins[name] = original })
}

func TestSyntheticInventoryOnly(t *testing.T) {
	for _, target := range []string{"networkd", "nft"} {
		t.Run(target, func(t *testing.T) {
			raw := fixture(pins[target])
			syntheticPin(t, target, raw)
			out, err := inspect(bytes.NewReader(raw), []string{"--target", target})
			if err != nil {
				t.Fatal(err)
			}
			var report inventory
			if err := json.Unmarshal(out, &report); err != nil {
				t.Fatal(err)
			}
			if report.Scope != "offline-direct-elf-inventory-only" || report.Schema != "nib.network-elf-inputs/v1" ||
				report.Target != target || report.ELF.Interpreter != "/lib64/ld-linux-x86-64.so.2" {
				t.Fatal("wrong inventory")
			}
			var fields map[string]any
			if err := json.Unmarshal(out, &fields); err != nil {
				t.Fatal(err)
			}
			count := 0
			for key, value := range fields {
				if b, ok := value.(bool); ok {
					count++
					if b {
						t.Fatalf("unexpected proof claim: %s", key)
					}
				}
			}
			if count != 8 || len(out) > 4096 {
				t.Fatal("claim or output bound changed")
			}
			again, err := inspect(bytes.NewReader(raw), []string{"--target", target})
			if err != nil || !bytes.Equal(out, again) {
				t.Fatal("nondeterministic inventory")
			}
			fragmented, err := inspect(iotest.OneByteReader(bytes.NewReader(raw)), []string{"--target", target})
			if err != nil || !bytes.Equal(out, fragmented) {
				t.Fatal("fragmented input changed inventory")
			}
		})
	}
}

type failingReader struct{}

func (failingReader) Read([]byte) (int, error) { return 0, errors.New("untrusted detail") }

func TestArgumentsAuthenticationSizeAndReadErrors(t *testing.T) {
	for _, args := range [][]string{nil, {"nft"}, {"--target", "unknown"}, {"--target", "nft", "--run"}, {"--file", "nft"}} {
		if out, err := inspect(failingReader{}, args); err != rejected || len(out) != 0 {
			t.Fatal("accepted arguments")
		}
	}
	for _, name := range []string{"networkd", "nft"} {
		for _, size := range []int{0, pins[name].Bytes - 1, pins[name].Bytes, pins[name].Bytes + 1} {
			if out, err := inspect(bytes.NewReader(make([]byte, size)), []string{"--target", name}); err != rejected || len(out) != 0 {
				t.Fatal("accepted unpinned input")
			}
		}
		reader := bytes.NewReader(make([]byte, pins[name].Bytes+100))
		if _, err := inspect(reader, []string{"--target", name}); err != rejected || reader.Len() != 99 {
			t.Fatal("read exceeded selected size plus one")
		}
	}
	if _, err := inspect(failingReader{}, []string{"--target", "nft"}); err != rejected {
		t.Fatal(err)
	}
	if deadline != 10*time.Second {
		t.Fatal("deadline changed")
	}
}

func TestMalformedMetadataAndLoaderViewReject(t *testing.T) {
	changes := map[string]func([]byte){
		"magic":               func(b []byte) { b[0] = 0 },
		"arch":                func(b []byte) { binary.LittleEndian.PutUint16(b[18:], uint16(elf.EM_AARCH64)) },
		"kind":                func(b []byte) { binary.LittleEndian.PutUint16(b[16:], uint16(elf.ET_EXEC)) },
		"no-interp":           func(b []byte) { binary.LittleEndian.PutUint32(b[120:], 0) },
		"wrong-interp":        func(b []byte) { b[385] = 'x' },
		"unterminated-interp": func(b []byte) { b[411] = 'x' },
		"embedded-nul":        func(b []byte) { b[388] = 0 },
		"segment-overflow":    func(b []byte) { binary.LittleEndian.PutUint64(b[128:], ^uint64(0)) },
		"size-overflow":       func(b []byte) { binary.LittleEndian.PutUint64(b[152:], ^uint64(0)) },
		"duplicate-interp":    func(b []byte) { binary.LittleEndian.PutUint32(b[64:], uint32(elf.PT_INTERP)) },
		"duplicate-dynamic":   func(b []byte) { binary.LittleEndian.PutUint32(b[64:], uint32(elf.PT_DYNAMIC)) },
		"no-dynamic":          func(b []byte) { binary.LittleEndian.PutUint32(b[176:], 0) },
		"no-section":          func(b []byte) { binary.LittleEndian.PutUint32(b[1092:], 0) },
		"duplicate-section":   func(b []byte) { binary.LittleEndian.PutUint32(b[1156:], uint32(elf.SHT_DYNAMIC)) },
		"offset-disagrees":    func(b []byte) { binary.LittleEndian.PutUint64(b[1112:], 833) },
		"size-disagrees":      func(b []byte) { binary.LittleEndian.PutUint64(b[1120:], 1) },
		"address-disagrees":   func(b []byte) { binary.LittleEndian.PutUint64(b[1104:], 833) },
		"bad-string-link":     func(b []byte) { binary.LittleEndian.PutUint32(b[1128:], 99) },
		"bad-string-offset":   func(b []byte) { binary.LittleEndian.PutUint64(b[840:], 9999) },
		"untrusted-string":    func(b []byte) { b[449] = '\n' },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			raw := fixture(pins["nft"])
			change(raw)
			if _, err := readMetadata(raw); err != rejected {
				t.Fatal("accepted malformed metadata")
			}
		})
	}
	for _, n := range []int{0, 6, 63, 1279} {
		if _, err := readMetadata(fixture(pins["nft"])[:n]); err != rejected {
			t.Fatal("accepted truncation")
		}
	}
}

func TestUnexpectedLibrariesPathsAndOrderingReject(t *testing.T) {
	for _, change := range []func(*pin){
		func(p *pin) { p.Needed = []string{"libc.so.6"} },
		func(p *pin) { p.Needed = []string{"libc.so.6", "libedit.so.2", "libnftables.so.1"} },
		func(p *pin) { p.Needed = append(append([]string{}, p.Needed...), "libc.so.6") },
		func(p *pin) { p.Runpath = []string{"$ORIGIN"} },
	} {
		t.Run("mismatch", func(t *testing.T) {
			p := pins["nft"]
			change(&p)
			raw := fixture(p)
			syntheticPin(t, "nft", raw)
			if _, err := inspect(bytes.NewReader(raw), []string{"--target", "nft"}); err != rejected {
				t.Fatal("accepted changed dependencies")
			}
		})
	}
	t.Run("rpath", func(t *testing.T) {
		p := pins["nft"]
		p.Runpath = []string{"/usr/lib"}
		raw := fixture(p)
		binary.LittleEndian.PutUint64(raw[832+len(p.Needed)*16:], uint64(elf.DT_RPATH))
		syntheticPin(t, "nft", raw)
		if _, err := inspect(bytes.NewReader(raw), []string{"--target", "nft"}); err != rejected {
			t.Fatal("accepted RPATH")
		}
	})
}

func TestPinsMatchExistingPreparerAndDocumentation(t *testing.T) {
	source, err := os.ReadFile("../../scripts/prepare_network_proof.py")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := os.ReadFile("../../docs/tasks/p3-t1-network-elf-inputs.md")
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range pins {
		for _, value := range []string{p.Path, p.SHA256, fmt.Sprint(p.Bytes)} {
			if !bytes.Contains(source, []byte(value)) || !bytes.Contains(doc, []byte(value)) {
				t.Fatal("pin documentation drift")
			}
		}
	}
	defaultConfig, err := os.ReadFile("../../guest/network/network.json")
	if err != nil || string(defaultConfig) != "{\"schema\":\"nib.guest-network/v1\",\"egress\":[]}\n" {
		t.Fatal("default activation changed")
	}
}

func TestBlockedStdinHasWholeProcessDeadline(t *testing.T) {
	if os.Getenv("NIB_ELF_DEADLINE_TEST") == "1" {
		os.Args = []string{"inventory", "--target", "nft"}
		main()
		return
	}
	child := exec.Command(os.Args[0], "-test.run=^TestBlockedStdinHasWholeProcessDeadline$")
	child.Env = append(os.Environ(), "NIB_ELF_DEADLINE_TEST=1")
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	child.Stdin = reader
	var output bytes.Buffer
	child.Stdout, child.Stderr = &output, &output
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "exit status 1") || output.Len() != 0 {
			t.Fatal("deadline did not fail closed")
		}
	case <-time.After(deadline + 5*time.Second):
		_ = child.Process.Kill()
		<-done
		t.Fatal("whole-process deadline absent")
	}
}

var _ io.Reader = failingReader{}

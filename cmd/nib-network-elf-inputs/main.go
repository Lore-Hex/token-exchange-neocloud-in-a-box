// SPDX-License-Identifier: Apache-2.0
// nib-network-elf-inputs inspects two pinned ELF files; it never executes them.
package main

import (
	"bytes"
	"crypto/sha256"
	"debug/elf"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"slices"
	"time"
)

const deadline = 10 * time.Second

var rejected = errors.New("network ELF inventory rejected")

type pin struct {
	Path    string   `json:"path"`
	Bytes   int      `json:"bytes"`
	SHA256  string   `json:"sha256"`
	Needed  []string `json:"needed"`
	Runpath []string `json:"runpath"`
}

// These are the existing prepare_network_proof.py executable identities.
var pins = map[string]pin{
	"networkd": {
		Path: "usr/lib/systemd/systemd-networkd", Bytes: 1669864,
		SHA256:  "e4ab77a36ee958f052b0100b60583716904a393159227e1e895469199e1fd8f9",
		Needed:  []string{"libsystemd-shared-255.so", "libc.so.6"},
		Runpath: []string{"/usr/lib/x86_64-linux-gnu/systemd"},
	},
	"nft": {
		Path: "usr/sbin/nft", Bytes: 26856,
		SHA256:  "3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7",
		Needed:  []string{"libnftables.so.1", "libedit.so.2", "libc.so.6"},
		Runpath: []string{},
	},
}

type metadata struct {
	Interpreter string   `json:"interpreter"`
	Needed      []string `json:"needed"`
	Rpath       []string `json:"rpath"`
	Runpath     []string `json:"runpath"`
}

type inventory struct {
	Schema            string   `json:"schema"`
	Scope             string   `json:"scope"`
	Target            string   `json:"target"`
	Executable        pin      `json:"executable"`
	ELF               metadata `json:"elf"`
	RuntimeClosure    bool     `json:"runtime_loader_closure_verified"`
	PackageExecuted   bool     `json:"package_binary_executed"`
	ImageModified     bool     `json:"image_modified"`
	NetworkActivated  bool     `json:"network_activated"`
	ProofCompleted    bool     `json:"proof_completed"`
	HardwareValidated bool     `json:"hardware_validated"`
	Confidential      bool     `json:"confidential"`
	Deployable        bool     `json:"deployable"`
}

func readMetadata(raw []byte) (metadata, error) {
	result := metadata{Needed: []string{}, Rpath: []string{}, Runpath: []string{}}
	f, err := elf.NewFile(bytes.NewReader(raw))
	if err != nil {
		return result, rejected
	}
	defer f.Close()
	if f.Class != elf.ELFCLASS64 || f.Data != elf.ELFDATA2LSB || f.Machine != elf.EM_X86_64 ||
		f.Type != elf.ET_DYN || len(f.Progs) == 0 || len(f.Progs) > 256 || len(f.Sections) > 256 {
		return result, rejected
	}
	var dynamic *elf.Prog
	for _, p := range f.Progs {
		if p.Off > uint64(len(raw)) || p.Filesz > uint64(len(raw))-p.Off {
			return result, rejected
		}
		switch p.Type {
		case elf.PT_INTERP:
			if result.Interpreter != "" || p.Filesz < 2 || p.Filesz > 1024 {
				return result, rejected
			}
			value := raw[p.Off : p.Off+p.Filesz]
			if value[len(value)-1] != 0 || bytes.IndexByte(value[:len(value)-1], 0) >= 0 {
				return result, rejected
			}
			result.Interpreter = string(value[:len(value)-1])
		case elf.PT_DYNAMIC:
			if dynamic != nil {
				return result, rejected
			}
			dynamic = p
		}
	}
	if result.Interpreter != "/lib64/ld-linux-x86-64.so.2" || dynamic == nil {
		return result, rejected
	}
	var section *elf.Section
	for _, s := range f.Sections {
		if s.Type == elf.SHT_DYNAMIC {
			if section != nil {
				return result, rejected
			}
			section = s
		}
	}
	// debug/elf reads dynamic sections. Bind that view to the loader's segment.
	if section == nil || section.Offset != dynamic.Off || section.Size != dynamic.Filesz ||
		section.Addr != dynamic.Vaddr || section.Size > 65536 {
		return result, rejected
	}
	for tag, destination := range map[elf.DynTag]*[]string{
		elf.DT_NEEDED: &result.Needed, elf.DT_RPATH: &result.Rpath, elf.DT_RUNPATH: &result.Runpath,
	} {
		values, err := f.DynValue(tag)
		if err != nil || len(values) > 32 {
			return result, rejected
		}
		strings, err := f.DynString(tag)
		if err != nil || len(strings) != len(values) {
			return result, rejected
		}
		for _, value := range strings {
			if len(value) == 0 || len(value) > 256 {
				return result, rejected
			}
			for _, c := range value {
				if c < 33 || c > 126 {
					return result, rejected
				}
			}
		}
		*destination = append(*destination, strings...)
	}
	return result, nil
}

func inspect(input io.Reader, args []string) ([]byte, error) {
	if len(args) != 2 || args[0] != "--target" {
		return nil, rejected
	}
	p, ok := pins[args[1]]
	if !ok {
		return nil, rejected
	}
	raw, err := io.ReadAll(io.LimitReader(input, int64(p.Bytes)+1))
	if err != nil || len(raw) != p.Bytes {
		return nil, rejected
	}
	sha := sha256.Sum256(raw)
	if hex.EncodeToString(sha[:]) != p.SHA256 {
		return nil, rejected
	}
	// Authentication precedes ELF parsing; this is not a general ELF admission API.
	meta, err := readMetadata(raw)
	if err != nil || !slices.Equal(meta.Needed, p.Needed) || len(meta.Rpath) != 0 ||
		!slices.Equal(meta.Runpath, p.Runpath) {
		return nil, rejected
	}
	report := inventory{Schema: "nib.network-elf-inputs/v1", Scope: "offline-direct-elf-inventory-only",
		Target: args[1], Executable: p, ELF: meta}
	encoded, err := json.Marshal(report)
	if err != nil || len(encoded) >= 4096 {
		return nil, rejected
	}
	return append(encoded, '\n'), nil
}

func main() {
	// The process owns no children or files; exit also terminates a blocked stdin read.
	timer := time.AfterFunc(deadline, func() { os.Exit(1) })
	defer timer.Stop()
	output, err := inspect(os.Stdin, os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "Network ELF inventory rejected; no execution attempted.")
		os.Exit(1)
	}
	if _, err := os.Stdout.Write(output); err != nil {
		os.Exit(1)
	}
}

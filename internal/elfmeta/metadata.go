// SPDX-License-Identifier: Apache-2.0
// Package elfmeta reads bounded ELF metadata without executing a loader.
package elfmeta

import (
	"bytes"
	"debug/elf"
	"encoding/binary"
	"errors"
	"strings"
)

const MaxBytes = 16 << 20

var ErrRejected = errors.New("ELF metadata rejected")

type Metadata struct {
	Interpreter string   `json:"interpreter"`
	Needed      []string `json:"needed"`
	Rpath       []string `json:"rpath"`
	Runpath     []string `json:"runpath"`
	Soname      []string `json:"soname"`
}

func printable(value string) bool {
	if len(value) == 0 || len(value) > 256 {
		return false
	}
	for _, c := range value {
		if c < 33 || c > 126 {
			return false
		}
	}
	return true
}

// Bounds precede debug/elf, including its section-name table allocation.
func bounded(raw []byte) bool {
	if len(raw) < 64 || len(raw) > MaxBytes || !bytes.Equal(raw[:7], []byte("\x7fELF\x02\x01\x01")) {
		return false
	}
	var h elf.Header64
	if binary.Read(bytes.NewReader(raw[:64]), binary.LittleEndian, &h) != nil ||
		h.Type != uint16(elf.ET_DYN) || h.Machine != uint16(elf.EM_X86_64) || h.Version != 1 ||
		h.Ehsize != 64 || h.Phentsize != 56 || h.Shentsize != 64 ||
		h.Phnum == 0 || h.Phnum > 256 || h.Shnum == 0 || h.Shnum > 256 || h.Shstrndx >= h.Shnum {
		return false
	}
	inside := func(off, size uint64) bool { return off <= uint64(len(raw)) && size <= uint64(len(raw))-off }
	if !inside(h.Phoff, uint64(h.Phnum)*56) || !inside(h.Shoff, uint64(h.Shnum)*64) {
		return false
	}
	for i := uint64(0); i < uint64(h.Shnum); i++ {
		var s elf.Section64
		if binary.Read(bytes.NewReader(raw[h.Shoff+i*64:]), binary.LittleEndian, &s) != nil ||
			s.Flags&uint64(elf.SHF_COMPRESSED) != 0 ||
			(i == uint64(h.Shstrndx) && i != 0 && s.Type != uint32(elf.SHT_STRTAB)) ||
			(s.Type != uint32(elf.SHT_NOBITS) && !inside(s.Off, s.Size)) ||
			(s.Type == uint32(elf.SHT_STRTAB) && s.Size > 1<<20) {
			return false
		}
	}
	return true
}

// Read accepts a restricted amd64 ET_DYN metadata shape, not executable safety
// or ABI compatibility. Authentication of input bytes is the caller's job.
func Read(raw []byte) (Metadata, error) {
	r := Metadata{Needed: []string{}, Rpath: []string{}, Runpath: []string{}, Soname: []string{}}
	if !bounded(raw) {
		return r, ErrRejected
	}
	f, err := elf.NewFile(bytes.NewReader(raw))
	if err != nil {
		return r, ErrRejected
	}
	defer f.Close()
	var dynamic *elf.Prog
	for _, p := range f.Progs {
		if p.Off > uint64(len(raw)) || p.Filesz > uint64(len(raw))-p.Off {
			return r, ErrRejected
		}
		switch p.Type {
		case elf.PT_INTERP:
			if r.Interpreter != "" || p.Filesz < 2 || p.Filesz > 257 {
				return r, ErrRejected
			}
			value := raw[p.Off : p.Off+p.Filesz]
			if value[len(value)-1] != 0 || !printable(string(value[:len(value)-1])) {
				return r, ErrRejected
			}
			r.Interpreter = string(value[:len(value)-1])
		case elf.PT_DYNAMIC:
			if dynamic != nil {
				return r, ErrRejected
			}
			dynamic = p
		}
	}
	var section *elf.Section
	for _, s := range f.Sections {
		// debug/elf also decompresses legacy .zdebug sections without the flag.
		// Reject before Data/DynString can substitute an expanded loader view.
		if strings.HasPrefix(s.Name, ".zdebug") {
			return r, ErrRejected
		}
		if s.Type == elf.SHT_DYNAMIC {
			if section != nil {
				return r, ErrRejected
			}
			section = s
		}
	}
	// debug/elf uses sections; bind the dynamic and string views to segments.
	if section == nil || dynamic == nil || section.Offset != dynamic.Off ||
		section.Size != dynamic.Filesz || section.Addr != dynamic.Vaddr ||
		section.Size == 0 || section.Size > 65536 || section.Size%16 != 0 ||
		section.Link >= uint32(len(f.Sections)) {
		return r, ErrRejected
	}
	terminated := false
	for off := section.Offset; off < section.Offset+section.Size; off += 16 {
		tag := binary.LittleEndian.Uint64(raw[off:])
		value := binary.LittleEndian.Uint64(raw[off+8:])
		if (terminated && (tag != 0 || value != 0)) || (tag == 0 && value != 0) {
			return r, ErrRejected
		}
		terminated = terminated || tag == 0
	}
	if !terminated {
		return r, ErrRejected
	}
	str := f.Sections[section.Link]
	addr, e1 := f.DynValue(elf.DT_STRTAB)
	size, e2 := f.DynValue(elf.DT_STRSZ)
	if e1 != nil || e2 != nil || len(addr) != 1 || len(size) != 1 ||
		str.Type != elf.SHT_STRTAB || str.Addr != addr[0] || str.Size != size[0] {
		return r, ErrRejected
	}
	mapped := func(s *elf.Section) int {
		count := 0
		for _, p := range f.Progs {
			if p.Type == elf.PT_LOAD && s.Offset >= p.Off && s.Offset-p.Off <= p.Filesz &&
				s.Size <= p.Filesz-(s.Offset-p.Off) && s.Addr >= p.Vaddr && s.Addr-p.Vaddr == s.Offset-p.Off {
				count++
			}
		}
		return count
	}
	if mapped(str) != 1 || mapped(section) != 1 {
		return r, ErrRejected
	}
	// Alternative dependency/audit mechanisms are outside this static contract.
	for _, tag := range []elf.DynTag{elf.DT_FILTER, elf.DT_AUXILIARY, elf.DT_AUDIT, elf.DT_DEPAUDIT, elf.DT_CONFIG, elf.DT_POSFLAG_1} {
		v, err := f.DynValue(tag)
		if err != nil || len(v) != 0 {
			return r, ErrRejected
		}
	}
	flags, err := f.DynValue(elf.DT_FLAGS_1)
	if err != nil || len(flags) > 1 || (len(flags) == 1 && flags[0]&uint64(elf.DF_1_NODEFLIB) != 0) {
		return r, ErrRejected
	}
	for tag, dst := range map[elf.DynTag]*[]string{elf.DT_NEEDED: &r.Needed,
		elf.DT_RPATH: &r.Rpath, elf.DT_RUNPATH: &r.Runpath, elf.DT_SONAME: &r.Soname} {
		v, err := f.DynValue(tag)
		if err != nil || len(v) > 32 {
			return r, ErrRejected
		}
		s, err := f.DynString(tag)
		if err != nil || len(s) != len(v) {
			return r, ErrRejected
		}
		for _, value := range s {
			if !printable(value) {
				return r, ErrRejected
			}
		}
		*dst = append(*dst, s...)
	}
	return r, nil
}

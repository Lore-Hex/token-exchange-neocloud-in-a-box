// SPDX-License-Identifier: Apache-2.0
package elfmeta

import (
	"debug/elf"
	"encoding/binary"
	"reflect"
	"testing"
)

// This is metadata-shaped synthetic data, never an executable fixture.
func fixture() []byte {
	b := make([]byte, 1024)
	copy(b, []byte("\x7fELF\x02\x01\x01"))
	u16 := func(at int, v uint16) { binary.LittleEndian.PutUint16(b[at:], v) }
	u32 := func(at int, v uint32) { binary.LittleEndian.PutUint32(b[at:], v) }
	u64 := func(at int, v uint64) { binary.LittleEndian.PutUint64(b[at:], v) }
	u16(16, uint16(elf.ET_DYN))
	u16(18, uint16(elf.EM_X86_64))
	u32(20, 1)
	u64(32, 64)
	u64(40, 768)
	u16(52, 64)
	u16(54, 56)
	u16(56, 2)
	u16(58, 64)
	u16(60, 3)
	str := []byte("\x00liba.so.1\x00libc.so.6\x00")
	copy(b[256:], str)
	entries := []struct{ tag, value uint64 }{
		{uint64(elf.DT_SONAME), 1}, {uint64(elf.DT_NEEDED), 11},
		{uint64(elf.DT_STRTAB), 256}, {uint64(elf.DT_STRSZ), uint64(len(str))}, {0, 0}, {0, 0},
	}
	for i, e := range entries {
		u64(384+i*16, e.tag)
		u64(392+i*16, e.value)
	}
	u32(64, uint32(elf.PT_LOAD))
	u64(64+32, uint64(len(b)))
	u64(64+40, uint64(len(b)))
	u32(120, uint32(elf.PT_DYNAMIC))
	u64(120+8, 384)
	u64(120+16, 384)
	u64(120+32, 96)
	u64(120+40, 96)
	u32(832+4, uint32(elf.SHT_DYNAMIC))
	u64(832+16, 384)
	u64(832+24, 384)
	u64(832+32, 96)
	u32(832+40, 2)
	u64(832+56, 16)
	u32(896+4, uint32(elf.SHT_STRTAB))
	u64(896+16, 256)
	u64(896+24, 256)
	u64(896+32, uint64(len(str)))
	return b
}

func TestLibraryWithoutInterpreter(t *testing.T) {
	m, err := Read(fixture())
	if err != nil || m.Interpreter != "" || !reflect.DeepEqual(m.Soname, []string{"liba.so.1"}) ||
		!reflect.DeepEqual(m.Needed, []string{"libc.so.6"}) || len(m.Rpath)+len(m.Runpath) != 0 {
		t.Fatalf("unexpected metadata: %+v %v", m, err)
	}
}

func TestBoundsAndSectionLoaderAgreement(t *testing.T) {
	mutations := map[string]func([]byte){
		"extended-sections": func(b []byte) { binary.LittleEndian.PutUint16(b[60:], 0) },
		"many-sections":     func(b []byte) { binary.LittleEndian.PutUint16(b[60:], 257) },
		"many-segments":     func(b []byte) { binary.LittleEndian.PutUint16(b[56:], 257) },
		"bad-shstr":         func(b []byte) { binary.LittleEndian.PutUint16(b[62:], 99) },
		"section-overflow":  func(b []byte) { binary.LittleEndian.PutUint64(b[40:], ^uint64(0)) },
		"segment-overflow":  func(b []byte) { binary.LittleEndian.PutUint64(b[32:], ^uint64(0)) },
		"compressed":        func(b []byte) { binary.LittleEndian.PutUint64(b[896+8:], uint64(elf.SHF_COMPRESSED)) },
		"huge-strings":      func(b []byte) { binary.LittleEndian.PutUint64(b[896+32:], 1<<30) },
		"string-address":    func(b []byte) { binary.LittleEndian.PutUint64(b[896+16:], 255) },
		"string-size":       func(b []byte) { binary.LittleEndian.PutUint64(b[384+56:], 5) },
		"not-load-mapped":   func(b []byte) { binary.LittleEndian.PutUint32(b[64:], uint32(elf.PT_NOTE)) },
		"string-offset":     func(b []byte) { binary.LittleEndian.PutUint64(b[392:], 9999) },
		"no-null": func(b []byte) {
			binary.LittleEndian.PutUint64(b[448:], uint64(elf.DT_DEBUG))
			binary.LittleEndian.PutUint64(b[464:], uint64(elf.DT_DEBUG))
		},
		"after-null": func(b []byte) {
			binary.LittleEndian.PutUint64(b[464:], uint64(elf.DT_NEEDED))
			binary.LittleEndian.PutUint64(b[472:], 11)
		},
		"null-value": func(b []byte) { b[456] = 1 },
		"audit":      func(b []byte) { binary.LittleEndian.PutUint64(b[384:], uint64(elf.DT_AUDIT)) },
		"filter":     func(b []byte) { binary.LittleEndian.PutUint64(b[384:], uint64(elf.DT_FILTER)) },
		"auxiliary":  func(b []byte) { binary.LittleEndian.PutUint64(b[384:], uint64(elf.DT_AUXILIARY)) },
		"nodeflib": func(b []byte) {
			binary.LittleEndian.PutUint64(b[384:], uint64(elf.DT_FLAGS_1))
			binary.LittleEndian.PutUint64(b[392:], uint64(elf.DF_1_NODEFLIB))
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			b := fixture()
			mutate(b)
			if _, err := Read(b); err != ErrRejected {
				t.Fatal("accepted unsupported or inconsistent ELF")
			}
		})
	}
	for _, b := range [][]byte{nil, fixture()[:63], fixture()[:900], make([]byte, MaxBytes+1)} {
		if _, err := Read(b); err != ErrRejected {
			t.Fatal("accepted unbounded/truncated data")
		}
	}
}

func FuzzRead(f *testing.F) {
	f.Add(fixture())
	f.Add([]byte("not ELF"))
	f.Fuzz(func(t *testing.T, b []byte) {
		if len(b) <= MaxBytes+1 {
			_, _ = Read(b)
		}
	})
}

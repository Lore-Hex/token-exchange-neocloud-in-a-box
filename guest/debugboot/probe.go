// SPDX-License-Identifier: Apache-2.0
// Package debugboot checks one debug guest's live root; it never qualifies a TEE.
package debugboot

import (
	"errors"
	"io"
	"strconv"
	"strings"
	"time"
)

const (
	Kernel       = "6.8.0-147-generic"
	MaxRootBytes = 1536 << 20
	RootSalt     = "89609461d8139622a4a5e5b22ac0a928626ceac08652e36004266d6c7acc3c16"
	PortName     = "org.nib.boot-proof"
	InitrdRecord = "NIB_DEBUG_BOOT_V1 INITRD\n"
	PassRecord   = "NIB_DEBUG_BOOT_V1 PASS\n"
	FailRecord   = "NIB_DEBUG_BOOT_V1 FAIL\n"
	cmdPrefix    = "root=/dev/mapper/root rootfstype=erofs ro roothash="
	cmdSuffix    = " systemd.unit=nib-debug-boot.target nib.debug-boot=v1"
)

var errRejected = errors.New("debug boot rejected")

func lowerHex(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func rootHash(raw []byte) (string, error) {
	s := string(raw)
	if !strings.HasSuffix(s, "\n") {
		return "", errRejected
	}
	s = strings.TrimSuffix(s, "\n")
	if len(s) != len(cmdPrefix)+64+len(cmdSuffix) || !strings.HasPrefix(s, cmdPrefix) || !strings.HasSuffix(s, cmdSuffix) {
		return "", errRejected
	}
	hash := s[len(cmdPrefix) : len(cmdPrefix)+64]
	if !lowerHex(hash) {
		return "", errRejected
	}
	return hash, nil
}

func decimal(s string, max uint64) (uint64, bool) {
	n, err := strconv.ParseUint(s, 10, 64)
	return n, err == nil && n <= max && strconv.FormatUint(n, 10) == s
}

type device struct{ major, minor uint64 }

func parseDevice(s string) (device, error) {
	a, b, ok := strings.Cut(s, ":")
	major, aOK := decimal(a, 4095)
	minor, bOK := decimal(b, (1<<20)-1)
	if !ok || !aOK || !bOK || major == 0 {
		return device{}, errRejected
	}
	return device{major, minor}, nil
}

func (d device) String() string {
	return strconv.FormatUint(d.major, 10) + ":" + strconv.FormatUint(d.minor, 10)
}

func readOnly(options string) bool {
	ro := 0
	for _, option := range strings.Split(options, ",") {
		if option == "rw" || option == "" {
			return false
		}
		if option == "ro" {
			ro++
		}
	}
	return ro == 1
}

// proc_pid_mountinfo(5): inspect PID1 as well as ourselves so a service's
// private read-only remount cannot masquerade as the real root mount.
func rootMount(raw []byte) (device, error) {
	if len(raw) == 0 || len(raw) > 65536 || raw[len(raw)-1] != '\n' {
		return device{}, errRejected
	}
	var result device
	count := 0
	for _, line := range strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 10 {
			return device{}, errRejected
		}
		if fields[4] != "/" {
			continue
		}
		count++
		separator := -1
		for i := 6; i < len(fields); i++ {
			if fields[i] == "-" {
				if separator != -1 {
					return device{}, errRejected
				}
				separator = i
			}
		}
		if separator < 6 || separator+4 != len(fields) || fields[3] != "/" || !readOnly(fields[5]) ||
			fields[separator+1] != "erofs" || fields[separator+2] != "/dev/mapper/root" || !readOnly(fields[separator+3]) {
			return device{}, errRejected
		}
		var err error
		result, err = parseDevice(fields[2])
		if err != nil {
			return device{}, err
		}
	}
	if count != 1 {
		return device{}, errRejected
	}
	return result, nil
}

type mapping struct {
	bytes        int64
	data, hashes device
}

// Linux v6.8 verity.rst: reject every optional feature, including zero-block
// shortcuts and check_at_most_once. This validates a live table, not a signature.
func table(raw []byte, hash string) (mapping, error) {
	if len(raw) == 0 || len(raw) > 4096 || strings.Count(string(raw), "\n") != 1 || raw[len(raw)-1] != '\n' || !lowerHex(hash) {
		return mapping{}, errRejected
	}
	f := strings.Fields(string(raw))
	if len(f) == 14 && f[13] == "0" {
		f = f[:13]
	}
	if len(f) != 13 || f[0] != "0" || f[2] != "verity" || f[3] != "1" || f[6] != "4096" || f[7] != "4096" ||
		f[9] != "1" || f[10] != "sha256" || f[11] != hash || f[12] != RootSalt {
		return mapping{}, errRejected
	}
	sectors, sectorsOK := decimal(f[1], MaxRootBytes/512)
	blocks, blocksOK := decimal(f[8], MaxRootBytes/4096)
	data, dataErr := parseDevice(f[4])
	hashes, hashErr := parseDevice(f[5])
	if !sectorsOK || !blocksOK || blocks == 0 || sectors != blocks*8 || dataErr != nil || hashErr != nil || data == hashes {
		return mapping{}, errRejected
	}
	return mapping{int64(blocks * 4096), data, hashes}, nil
}

func validStatus(raw []byte, m mapping) bool {
	return string(raw) == "0 "+strconv.FormatInt(m.bytes/512, 10)+" verity V\n"
}

func readAllRoot(reader io.Reader, size int64, deadline time.Time) error {
	if size < 4096 || size > MaxRootBytes || size%4096 != 0 {
		return errRejected
	}
	buffer := make([]byte, 65536)
	remaining := size
	for remaining > 0 {
		if !time.Now().Before(deadline) {
			return errRejected
		}
		n, err := io.ReadFull(reader, buffer[:min(int64(len(buffer)), remaining)])
		if err != nil {
			return errRejected
		}
		remaining -= int64(n)
	}
	var extra [1]byte
	n, err := reader.Read(extra[:])
	if n != 0 || err != io.EOF || !time.Now().Before(deadline) {
		return errRejected
	}
	return nil
}

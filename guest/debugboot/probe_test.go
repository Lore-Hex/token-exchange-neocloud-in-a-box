// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"bytes"
	"errors"
	"io"
	"strconv"
	"strings"
	"testing"
	"time"
)

var testHash = strings.Repeat("1", 64)

func TestFailureRecordsAreFixedAndCannotBecomePass(t *testing.T) {
	names := strings.Fields("INITRD_STAGE INITRD_MARKER KERNEL PID1 CMDLINE PID1_MOUNT SELF_MOUNT MOUNT_MATCH DM_NAME DM_SUSPENDED DM_READONLY TABLE_QUERY TABLE_FORMAT ROOT_OPEN ROOT_READ TABLE_RECHECK STATUS_QUERY STATUS_FORMAT DEADLINE")
	if len(names) != len(failureNames) {
		t.Fatal("failure stage contract changed")
	}
	for i, name := range names {
		want := "NIB_DEBUG_BOOT_V1 FAIL " + name + "\n"
		if failureNames[i] != name || failureRecord(failureStage(i)) != want || len(want) > 64 {
			t.Fatal("unexpected diagnostic record")
		}
	}
	for i := len(names); i < 256; i++ {
		if failureRecord(failureStage(i)) != FailRecord {
			t.Fatal("unknown stage exposed data or accepted success")
		}
	}
}

func testTable() string {
	return "0 16 verity 1 254:1 254:2 4096 4096 2 1 sha256 " + testHash + " " + RootSalt + "\n"
}

func testMount() string {
	return "29 1 253:0 / / ro,relatime shared:1 - erofs /dev/mapper/root ro,user_xattr\n"
}

func TestExactDebugCommandLine(t *testing.T) {
	valid := cmdPrefix + testHash + cmdSuffix + "\n"
	got, err := rootHash([]byte(valid))
	if err != nil || got != testHash {
		t.Fatal(got, err)
	}
	for _, raw := range []string{"", valid + "\n", strings.TrimSuffix(valid, "\n"), valid + "roothash=" + testHash,
		strings.Replace(valid, testHash, strings.Repeat("A", 64), 1), strings.Replace(valid, " ro ", " rw ", 1),
		strings.Replace(valid, cmdSuffix, "", 1), strings.Replace(valid, "nib.debug-boot=v1", "nib.debug-boot=v2", 1),
		strings.Replace(valid, "nib-debug-boot.target", "multi-user.target", 1),
		strings.Replace(valid, "roothash=", "systemd.verity_root_options=ignore_corruption roothash=", 1)} {
		if _, err := rootHash([]byte(raw)); err == nil {
			t.Fatal("accepted nonfixed command line")
		}
	}
}

func TestRootMount(t *testing.T) {
	valid := testMount()
	dev, err := rootMount([]byte(valid + "31 29 0:20 / /run rw,nosuid - tmpfs tmpfs rw,size=1m\n"))
	if err != nil || dev != (device{253, 0}) {
		t.Fatal(dev, err)
	}
	for _, raw := range []string{"", valid + valid, strings.TrimSuffix(valid, "\n"),
		strings.Replace(valid, "253:0", "0:0", 1), strings.Replace(valid, "253:0", "4096:0", 1),
		strings.Replace(valid, "253:0", "0253:0", 1), strings.Replace(valid, " / / ", " /subtree / ", 1),
		strings.Replace(valid, " / / ", " / /sysroot ", 1), strings.Replace(valid, "ro,relatime", "rw,relatime", 1),
		strings.Replace(valid, "ro,relatime", "ro,rw", 1), strings.Replace(valid, "ro,user_xattr", "rw,user_xattr", 1),
		strings.Replace(valid, " erofs ", " ext4 ", 1), strings.Replace(valid, "/dev/mapper/root", "/dev/vda1", 1),
		strings.Replace(valid, " - ", " - - ", 1), valid + strings.Repeat("x", 65536)} {
		if _, err := rootMount([]byte(raw)); err == nil {
			t.Fatal("accepted invalid mounted root")
		}
	}
}

func TestVerityTableAndStatus(t *testing.T) {
	valid := testTable()
	m, err := table([]byte(valid), testHash)
	if err != nil || m.bytes != 8192 || m.data != (device{254, 1}) || m.hashes != (device{254, 2}) {
		t.Fatal(m, err)
	}
	if _, err := table([]byte(strings.TrimSuffix(valid, "\n")+" 0\n"), testHash); err != nil {
		t.Fatal(err)
	}
	if !validStatus([]byte("0 16 verity V\n"), m) {
		t.Fatal("valid status rejected")
	}
	for _, raw := range []string{"", "0 16 verity C\n", "0 16 verity V\n0 16 verity V\n", "0 15 verity V\n", "1 16 verity V\n", "0 16 verity V"} {
		if validStatus([]byte(raw), m) {
			t.Fatal("invalid status accepted")
		}
	}
	mutations := []string{"", valid + valid, strings.TrimSuffix(valid, "\n"),
		strings.Replace(valid, "0 16", "1 16", 1), strings.Replace(valid, "0 16", "0 8", 1),
		strings.Replace(valid, "0 16", "0 999999999999999999999999999", 1),
		strings.Replace(valid, "0 16", "0 016", 1), strings.Replace(valid, "verity 1", "verity 0", 1),
		strings.Replace(valid, "verity 1", "linear 1", 1), strings.Replace(valid, "254:2", "254:1", 1),
		strings.Replace(valid, "254:1", "/dev/vda1", 1), strings.Replace(valid, "4096 4096", "512 4096", 1),
		strings.Replace(valid, "2 1 sha256", "2 0 sha256", 1), strings.Replace(valid, "sha256", "sha512", 1),
		strings.Replace(valid, testHash, strings.Repeat("2", 64), 1), strings.Replace(valid, RootSalt, "-", 1)}
	for _, feature := range []string{"ignore_corruption", "ignore_zero_blocks", "check_at_most_once", "restart_on_corruption", "unknown"} {
		mutations = append(mutations, strings.TrimSuffix(valid, "\n")+" 1 "+feature+"\n")
	}
	for _, raw := range mutations {
		if _, err := table([]byte(raw), testHash); err == nil {
			t.Fatal("unsafe or malformed verity table accepted")
		}
	}
}

type brokenReader struct{}

func (brokenReader) Read([]byte) (int, error) { return 0, errors.New("do not expose device content") }

func TestFullBoundedRootRead(t *testing.T) {
	deadline := time.Now().Add(time.Second)
	observed, err := readAllRoot(bytes.NewReader(make([]byte, 8192)), 8192, deadline)
	if err != nil || observed.reason != readOK || observed.bytes != 8192 {
		t.Fatal(err)
	}
	for _, size := range []int64{-1, 0, 4095, 4097, MaxRootBytes + 4096} {
		observed, err := readAllRoot(bytes.NewReader(nil), size, deadline)
		if err == nil || observed.reason != readSize || observed.bytes != 0 {
			t.Fatal("invalid size accepted")
		}
	}
	for _, n := range []int{0, 4095, 4097} {
		observed, err := readAllRoot(bytes.NewReader(make([]byte, n)), 4096, deadline)
		want := readShort
		if n > 4096 {
			want = readExtra
		}
		if err == nil || observed.reason != want || observed.bytes != min(int64(n), 4096) {
			t.Fatal("truncated or excess device accepted")
		}
	}
	observed, err = readAllRoot(brokenReader{}, 4096, deadline)
	if err == nil || observed.reason != readIO || observed.bytes != 0 {
		t.Fatal("read failure accepted")
	}
	observed, err = readAllRoot(bytes.NewReader(make([]byte, 4096)), 4096, time.Now().Add(-time.Second))
	if err == nil || observed.reason != readDeadline || observed.bytes != 0 {
		t.Fatal("read failure or timeout accepted")
	}
}

type readFunc func([]byte) (int, error)

func (f readFunc) Read(p []byte) (int, error) { return f(p) }

func TestRootReadPartialCountAndMandatoryEOF(t *testing.T) {
	deadline := time.Now().Add(time.Second)
	observed, err := readAllRoot(bytes.NewReader(make([]byte, 65536+7)), 65536+4096, deadline)
	if err == nil || observed.reason != readShort || observed.bytes != 65536+7 {
		t.Fatal("partial read count was lost")
	}
	observed, err = readAllRoot(readFunc(func(p []byte) (int, error) {
		return copy(p, []byte{0, 0, 0}), errors.New("private-canary")
	}), 4096, deadline)
	if err == nil || observed.reason != readIO || observed.bytes != 3 || strings.Contains(observed.record(), "private-canary") {
		t.Fatal("partial I/O failure lost or exposed")
	}
	for _, ending := range []error{nil, errors.New("private-canary")} {
		input := bytes.NewReader(make([]byte, 4096))
		observed, err := readAllRoot(readFunc(func(p []byte) (int, error) {
			n, err := input.Read(p)
			if err == io.EOF {
				return 0, ending
			}
			return n, err
		}), 4096, deadline)
		if err == nil || observed.reason != readEOF || observed.bytes != 4096 {
			t.Fatal("missing EOF accepted")
		}
	}
}

func TestRootReadRecordFitsExistingProofBound(t *testing.T) {
	for reason := readSize; reason <= readClose; reason++ {
		for _, count := range []int64{0, 3, MaxRootBytes} {
			for _, seconds := range []int64{0, 25} {
				r := rootReadObservation{reason, count, seconds}
				want := "NIB_DEBUG_BOOT_V1 R " + strconv.Itoa(int(reason)) + " " + strconv.FormatInt(count, 10) + " " + strconv.FormatInt(seconds, 10) + "\n"
				if r.record() != want || len(InitrdRecord+r.record()) > 62 {
					t.Fatal("changed diagnostic grammar or proof bound")
				}
			}
		}
	}
	for _, r := range []rootReadObservation{{readOK, 0, 0}, {readClose + 1, 0, 0},
		{readSize, -1, 0}, {readSize, MaxRootBytes + 1, 0}, {readSize, 0, -1}, {readSize, 0, 26}} {
		if r.record() != failureRecord(failedRootRead) {
			t.Fatal("out-of-range diagnostic accepted")
		}
	}
}

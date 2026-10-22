//go:build linux && amd64

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const dmsetup = "/usr/sbin/dmsetup"

var portLeaf = regexp.MustCompile(`^vport[0-9]{1,3}p[0-9]{1,3}$`)

func statDevice(info os.FileInfo) device {
	n := uint64(info.Sys().(*syscall.Stat_t).Rdev)
	// Linux new_encode_dev / glibc gnu_dev_major/minor encoding.
	return device{(n >> 8 & 0xfff) | (n >> 32 & 0xfffff000), (n & 0xff) | (n >> 12 & 0xffffff00)}
}

func openDevice(path string, want device, char bool, flags int) (*os.File, error) {
	fd, err := syscall.Open(path, flags|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errRejected
	}
	f := os.NewFile(uintptr(fd), path)
	info, err := f.Stat()
	mode := os.ModeDevice
	if char {
		mode |= os.ModeCharDevice
	}
	if err != nil || info.Mode()&os.ModeType != mode || statDevice(info) != want {
		f.Close()
		return nil, errRejected
	}
	return f, nil
}

func port() (*os.File, error) {
	dir, err := os.Open("/sys/class/virtio-ports")
	if err != nil {
		return nil, errRejected
	}
	defer dir.Close()
	names, err := dir.Readdirnames(5)
	if err != nil && err != io.EOF || len(names) == 0 || len(names) > 4 {
		return nil, errRejected
	}
	selected := ""
	var dev device
	for _, name := range names {
		if !portLeaf.MatchString(name) {
			return nil, errRejected
		}
		raw, err := readBounded("/sys/class/virtio-ports/"+name+"/name", 128)
		if err != nil {
			return nil, err
		}
		if string(raw) != PortName+"\n" {
			continue
		}
		if selected != "" {
			return nil, errRejected
		}
		selected = name
		raw, err = readBounded("/sys/class/virtio-ports/"+name+"/dev", 32)
		if err != nil || !bytes.HasSuffix(raw, []byte("\n")) {
			return nil, errRejected
		}
		dev, err = parseDevice(strings.TrimSuffix(string(raw), "\n"))
		if err != nil {
			return nil, err
		}
	}
	if selected == "" {
		return nil, errRejected
	}
	return openDevice("/dev/"+selected, dev, true, syscall.O_WRONLY)
}

func liveRoot(stage *failureStage) (device, error) {
	var mounts [2]device
	for i, path := range []string{"/proc/1/mountinfo", "/proc/self/mountinfo"} {
		*stage = []failureStage{failedPID1Mount, failedSelfMount}[i]
		raw, err := readBounded(path, 65536)
		if err != nil {
			return device{}, err
		}
		mounts[i], err = rootMount(raw)
		if err != nil {
			return device{}, err
		}
	}
	*stage = failedMountMatch
	if mounts[0] != mounts[1] {
		return device{}, errRejected
	}
	dev := mounts[0]
	for _, check := range []struct {
		path, expected string
		stage          failureStage
	}{{"dm/name", "root\n", failedDMName}, {"dm/suspended", "0\n", failedDMSuspended}, {"ro", "1\n", failedDMReadonly}} {
		*stage = check.stage
		raw, err := readBounded("/sys/dev/block/"+dev.String()+"/"+check.path, 32)
		if err != nil || string(raw) != check.expected {
			return device{}, errRejected
		}
	}
	return dev, nil
}

func query(ctx context.Context, operation string) ([]byte, error) {
	if operation != "table" && operation != "status" {
		return nil, errRejected
	}
	raw, err := readBounded(dmsetup, 175160)
	digest := sha256.Sum256(raw)
	if err != nil || len(raw) != 175160 || hex.EncodeToString(digest[:]) != "684ad515cc43fee7f6c6875197b017caa98ebb34d7fd2ae9081e68b870e52410" {
		return nil, errRejected
	}
	// No --target filter: an unexpected extra target must remain visible.
	return execute(ctx, dmsetup, operation, "root")
}

func check(ctx context.Context, deadline time.Time, stage *failureStage, read *rootReadObservation) error {
	*stage = failedInitrdMarker
	if _, err := os.Lstat("/etc/initrd-release"); !os.IsNotExist(err) {
		return errRejected
	}
	for _, check := range []struct {
		path, expected string
		stage          failureStage
	}{{"/proc/sys/kernel/osrelease", Kernel + "\n", failedKernel}, {"/proc/1/comm", "systemd\n", failedPID1}} {
		*stage = check.stage
		raw, err := readBounded(check.path, 128)
		if err != nil || string(raw) != check.expected {
			return errRejected
		}
	}
	*stage = failedCmdline
	raw, err := readBounded("/proc/cmdline", 4096)
	if err != nil {
		return err
	}
	hash, err := rootHash(raw)
	if err != nil {
		return err
	}
	dev, err := liveRoot(stage)
	if err != nil {
		return err
	}
	*stage = failedTableQuery
	before, err := query(ctx, "table")
	if err != nil {
		return err
	}
	*stage = failedTableFormat
	m, err := table(before, hash)
	if err != nil || m.data == dev || m.hashes == dev {
		return errRejected
	}
	// Read every mapped data byte. V alone only covers checks performed so far.
	*stage = failedRootOpen
	root, err := openDevice("/dev/dm-"+strconv.FormatUint(dev.minor, 10), dev, false, syscall.O_RDONLY)
	if err != nil {
		return err
	}
	*stage = failedRootRead
	*read, err = readAllRoot(root, m.bytes, deadline)
	closeErr := root.Close()
	if err == nil && closeErr != nil {
		read.reason = readClose
	}
	if err != nil || closeErr != nil {
		return errRejected
	}
	*stage = failedTableRecheck
	after, err := query(ctx, "table")
	if err != nil || !bytes.Equal(before, after) {
		return errRejected
	}
	*stage = failedStatusQuery
	status, err := query(ctx, "status")
	if err != nil {
		return errRejected
	}
	*stage = failedStatusFormat
	if !validStatus(status, m) {
		return errRejected
	}
	current, err := liveRoot(stage)
	if err != nil {
		return err
	}
	*stage = failedMountMatch
	if current != dev {
		return errRejected
	}
	*stage = failedDeadline
	if !time.Now().Before(deadline) {
		return errRejected
	}
	return nil
}

func stageInitrd() error {
	if _, err := os.Stat("/etc/initrd-release"); err != nil {
		return errRejected
	}
	var fs syscall.Statfs_t
	if syscall.Statfs("/run", &fs) != nil || fs.Type != 0x01021994 { // Linux TMPFS_MAGIC.
		return errRejected
	}
	self, err := os.Stat("/proc/self/exe")
	if err != nil {
		return errRejected
	}
	original, err := os.Lstat("/usr/libexec/nib-debug-boot")
	if err != nil || !original.Mode().IsRegular() || !os.SameFile(self, original) {
		return errRejected
	}
	raw, err := readBounded("/usr/libexec/nib-debug-boot", 8<<20)
	if err != nil {
		return err
	}
	before, err := os.Lstat("/run")
	if err != nil || !before.IsDir() {
		return errRejected
	}
	run, err := os.OpenRoot("/run")
	if err != nil {
		return errRejected
	}
	defer run.Close()
	after, err := run.Stat(".")
	if err != nil || !os.SameFile(before, after) {
		return errRejected
	}
	return stageAt(run, raw)
}

// Run has no configurable paths, command, network or success override.
// It is linked only into the separately tagged debug command, never a gate.
func Run() int {
	staging := len(os.Args) == 2 && os.Args[1] == "--stage-initrd"
	if (len(os.Args) != 1 && !staging) || os.Geteuid() != 0 || os.Getegid() != 0 {
		return 1
	}
	// Also terminate blocked device reads; systemd supplies the outer kill bound.
	timer := time.AfterFunc(25*time.Second, func() { os.Exit(1) })
	defer timer.Stop()
	deadline := time.Now().Add(24 * time.Second)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	raw, err := readBounded("/proc/cmdline", 4096)
	if err != nil {
		return 1
	}
	if _, err = rootHash(raw); err != nil {
		return 1
	}
	output, err := port()
	for attempts := 0; err != nil && attempts < 40; attempts++ {
		time.Sleep(100 * time.Millisecond)
		output, err = port()
	}
	if err != nil {
		return 1
	}
	defer output.Close()
	stage := failedInitrd
	var read rootReadObservation
	record, result := FailRecord, 1
	if staging && stageInitrd() == nil {
		record, result = InitrdRecord, 0
	} else if !staging && check(ctx, deadline, &stage, &read) == nil {
		record, result = PassRecord, 0
	}
	if result != 0 {
		record = failureRecord(stage)
		if stage == failedRootRead {
			record = read.record()
		}
	}
	n, err := output.Write([]byte(record))
	if err != nil || n != len(record) {
		return 1
	}
	return result
}

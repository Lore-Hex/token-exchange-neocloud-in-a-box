//go:build linux && amd64

// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
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

// Exact payload members of image/inputs/guest-runtime-ubuntu2404.json.
// Parity with the installed-guest CLI probe is a required software test.
const executableSHA = "9c269a22c3f4710cdb115ce21d72c1c3b8e9c9e2db9cf4a391b510ed1a1dea42"
const librarySHA = "e757a59f8241830553da3f99f717ddd44d47973ca756a4fa3feac78012dce896"
const library = "/usr/lib/x86_64-linux-gnu/libnvat.so.2.0.0"
const libraryAlias = "/usr/lib/x86_64-linux-gnu/libnvat.so.2"

// Read-only EROFS is an accidental-host guard, not authentication of its image.
func readPinned(path string, size int64, expected string) error {
	if path != executable && path != library {
		return errRejected
	}
	fd, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return errRejected
	}
	defer func() { _ = syscall.Close(fd) }()
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for i, part := range parts {
		var info syscall.Stat_t
		var fs syscall.Statfs_t
		if syscall.Fstat(fd, &info) != nil || syscall.Fstatfs(fd, &fs) != nil || info.Uid != 0 || info.Gid != 0 || info.Mode&0o022 != 0 || fs.Type != 0xe0f5e1e2 || fs.Flags&1 == 0 {
			return errRejected
		}
		flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK | syscall.O_CLOEXEC
		if i < len(parts)-1 {
			flags |= syscall.O_DIRECTORY
		}
		next, err := syscall.Openat(fd, part, flags, 0)
		if err != nil {
			return errRejected
		}
		_ = syscall.Close(fd)
		fd = next
	}
	var before, after syscall.Stat_t
	var fs syscall.Statfs_t
	if syscall.Fstat(fd, &before) != nil || syscall.Fstatfs(fd, &fs) != nil || before.Mode != syscall.S_IFREG|0o755 || before.Uid != 0 || before.Gid != 0 || before.Nlink != 1 || before.Size != size || fs.Type != 0xe0f5e1e2 || fs.Flags&1 == 0 {
		return errRejected
	}
	dup, err := syscall.Dup(fd)
	if err != nil {
		return errRejected
	}
	f := os.NewFile(uintptr(dup), "appraiser-input")
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, size+1))
	closed := f.Close()
	if err != nil || closed != nil || n != size || hex.EncodeToString(h.Sum(nil)) != expected || syscall.Fstat(fd, &after) != nil || before != after {
		return errRejected
	}
	return nil
}

func privilegeStatus(raw []byte, uid, gid int) bool {
	if len(raw) == 0 || len(raw) > 16384 || uid <= 0 || gid <= 0 {
		return false
	}
	fields := map[string]string{}
	for _, line := range strings.Split(string(raw), "\n") {
		name, value, ok := strings.Cut(line, ":")
		if ok {
			if _, present := fields[name]; present {
				return false
			}
			fields[name] = strings.TrimSpace(value)
		}
	}
	for _, name := range []string{"Uid", "Gid", "Groups", "CapInh", "CapPrm", "CapEff", "CapAmb", "NoNewPrivs", "Seccomp"} {
		if _, present := fields[name]; !present {
			return false
		}
	}
	for _, name := range []string{"CapInh", "CapPrm", "CapEff", "CapAmb"} {
		if fields[name] != "0000000000000000" {
			return false
		}
	}
	for name, id := range map[string]int{"Uid": uid, "Gid": gid} {
		ids := strings.Fields(fields[name])
		if len(ids) != 4 {
			return false
		}
		for _, actual := range ids {
			if actual != strconv.Itoa(id) {
				return false
			}
		}
	}
	return fields["NoNewPrivs"] == "1" && fields["Seccomp"] == "2" && fields["Groups"] == ""
}

func runtimeCheck() error {
	f, err := os.Open("/proc/self/status")
	if err != nil {
		return errRejected
	}
	status, err := io.ReadAll(io.LimitReader(f, 16385))
	closed := f.Close()
	if err != nil || closed != nil || !privilegeStatus(status, os.Geteuid(), os.Getegid()) || os.Getuid() != os.Geteuid() || os.Getgid() != os.Getegid() {
		return errRejected
	}
	if readPinned(executable, 1128696, executableSHA) != nil || readPinned(library, 26794312, librarySHA) != nil {
		return errRejected
	}
	if link, err := os.Readlink(libraryAlias); err != nil || link != "libnvat.so.2.0.0" {
		return errRejected
	}
	if _, err := os.Lstat("/etc/ld.so.preload"); !os.IsNotExist(err) {
		return errRejected
	}
	var fs syscall.Statfs_t
	var info syscall.Stat_t
	if syscall.Lstat("/tmp", &info) != nil || syscall.Statfs("/tmp", &fs) != nil || info.Mode != syscall.S_IFDIR|0o1777 || info.Uid != 0 || info.Gid != 0 || fs.Type != 0x01021994 {
		return errRejected
	}
	return nil
}

// Run is diagnostic-only and is not called by nib-gpu-gate. It accepts no
// executable, endpoint, token, evidence, policy, credential or runtime override.
func Run(ctx context.Context, args []string) Report {
	r := report()
	if len(args) != 3 || args[0] != "--collect-and-appraise" || args[1] != "--deadline" || !regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`).MatchString(args[2]) {
		return r
	}
	wall, err := time.Parse(time.RFC3339, args[2])
	if err != nil || time.Until(wall) <= cleanupReserve || time.Until(wall) > MaxDuration {
		return r
	}
	ctx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	defer func() { cancel(); <-done }()
	go func() {
		defer close(done)
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if !time.Now().Before(wall.Add(-cleanupReserve)) {
					cancel()
					return
				}
			}
		}
	}()
	return collect(ctx, wall, "/tmp", operations{runtimeCheck, execute, time.Now})
}

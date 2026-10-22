//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"bytes"
	"context"
	"io"
	"os"
	"os/exec"
	"syscall"
	"time"
)

type boundedOutput struct{ bytes.Buffer }

func (b *boundedOutput) Write(p []byte) (int, error) {
	if len(p) > 4096-b.Len() {
		return 0, errRejected
	}
	return b.Buffer.Write(p)
}

// Only the pinned dmsetup's single-device, read-only table/status operations
// reach this helper. Bound both streams and kill the whole group on every exit.
func execute(ctx context.Context, executable string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, args...)
	cmd.Env = []string{"PATH=/usr/bin:/usr/sbin", "LANG=C", "LC_ALL=C"}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	var stdout, stderr boundedOutput
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Start(); err != nil {
		return nil, errRejected
	}
	err := cmd.Wait()
	cleanup := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	if cleanup != syscall.ESRCH || err != nil || ctx.Err() != nil || stderr.Len() != 0 {
		return nil, errRejected
	}
	return stdout.Bytes(), nil
}

func readBounded(path string, limit int64) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errRejected
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, err := f.Stat()
	// procfs/sysfs attributes may advertise 0 or 4096 bytes regardless of their
	// actual content. The read limit, not st_size, bounds these virtual files.
	if err != nil || !info.Mode().IsRegular() || limit <= 0 {
		return nil, errRejected
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, errRejected
	}
	return raw, nil
}

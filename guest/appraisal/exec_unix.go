//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"bytes"
	"context"
	"os/exec"
	"runtime"
	"syscall"
	"time"
)

type boundedOutput struct {
	buffer bytes.Buffer
	cancel context.CancelFunc
}

func (b *boundedOutput) Write(p []byte) (int, error) {
	if len(p) > limit-b.buffer.Len() {
		b.cancel()
		return 0, errRejected
	}
	return b.buffer.Write(p)
}

// Same owned-process-group discipline as the guest network/readback helpers.
// No shell, inherited environment, raw logs or unbounded descendant pipes.
func execute(ctx context.Context, binary string, args, env []string) ([]byte, bool, error) {
	// Linux parent-death signalling belongs to the creating OS thread.
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Env = env
	cmd.SysProcAttr = processAttributes()
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	out, stderr := boundedOutput{cancel: cancel}, boundedOutput{cancel: cancel}
	cmd.Stdout, cmd.Stderr = &out, &stderr
	if cmd.Start() != nil {
		return nil, true, errRejected
	}
	err := cmd.Wait()
	cleanup := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	if cleanup != syscall.ESRCH || err != nil || ctx.Err() != nil || stderr.buffer.Len() != 0 {
		return nil, cleanup == syscall.ESRCH, errRejected
	}
	return out.buffer.Bytes(), true, nil
}

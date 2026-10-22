//go:build (linux || darwin) && !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"context"
	"os/exec"
	"syscall"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

// Do not embed Buffer: its promoted ReadFrom bypasses Write under io.Copy.
type networkOutput struct{ buffer bytes.Buffer }

func (b *networkOutput) Write(p []byte) (int, error) {
	if len(p) > nftables.ReadbackLimit-b.buffer.Len() {
		return 0, errNetwork
	}
	return b.buffer.Write(p)
}

// No shell, inherited environment, raw logging or unbounded descendant pipes.
// The production caller supplies only the fixed verified nft executable/argv.
func executeNetwork(ctx context.Context, executable string, args []string, input []byte) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, args...)
	cmd.Env = []string{"PATH=/usr/sbin:/usr/bin", "LANG=C", "LC_ALL=C"}
	cmd.Stdin = bytes.NewReader(input)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	var stdout, stderr networkOutput
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Start(); err != nil {
		return nil, errNetwork
	}
	err := cmd.Wait()
	cleanup := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	if cleanup != syscall.ESRCH || err != nil || ctx.Err() != nil || stderr.buffer.Len() != 0 {
		return nil, errNetwork
	}
	return stdout.buffer.Bytes(), nil
}

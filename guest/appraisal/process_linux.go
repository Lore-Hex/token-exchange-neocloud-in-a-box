//go:build linux

// SPDX-License-Identifier: AGPL-3.0-or-later
package appraisal

import "syscall"

func processAttributes() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
}

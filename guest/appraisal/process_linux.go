//go:build linux

// SPDX-License-Identifier: Apache-2.0
package appraisal

import "syscall"

func processAttributes() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
}

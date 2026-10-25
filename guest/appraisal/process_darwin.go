//go:build darwin

// SPDX-License-Identifier: Apache-2.0
package appraisal

import "syscall"

// Darwin supports synthetic process tests, never the deployed collector.
func processAttributes() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true}
}

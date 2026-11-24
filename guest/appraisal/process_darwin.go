//go:build darwin

// SPDX-License-Identifier: AGPL-3.0-or-later
package appraisal

import "syscall"

// Darwin supports synthetic process tests, never the deployed collector.
func processAttributes() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true}
}

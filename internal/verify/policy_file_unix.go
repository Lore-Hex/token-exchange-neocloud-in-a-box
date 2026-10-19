//go:build unix

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"os"
	"syscall"
)

// Nonblocking open prevents a FIFO swap from hanging before f.Stat can reject
// it. O_NOFOLLOW also rejects final-component symlinks. Policy is local input;
// neither network filesystems nor adversarial device drivers are supported.
func openPolicyFile(path string) (*os.File, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), path), nil
}

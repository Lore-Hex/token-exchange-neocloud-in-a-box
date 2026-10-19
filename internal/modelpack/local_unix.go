//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"os"
	"syscall"
)

func openLocalWeight(root *os.Root, name string) (*os.File, error) {
	// NOFOLLOW rejects a leaf link swap; NONBLOCK avoids waiting forever if a
	// regular file is replaced by a FIFO between inventory and open. Stat and
	// identity checks still run on the opened descriptor before any reads.
	return root.OpenFile(name, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}

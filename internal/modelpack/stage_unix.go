//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"errors"
	"os"
	"syscall"
)

func lockStaging(root *os.Root, before os.FileInfo) (*os.File, error) {
	f, err := root.Open(".")
	if err != nil {
		return nil, errors.New("modelpack: cannot open staging lock")
	}
	i, err := f.Stat()
	if err != nil || !os.SameFile(before, i) || !i.IsDir() || i.Mode().Perm() != 0700 {
		f.Close()
		return nil, errors.New("modelpack: staging directory identity changed")
	}
	stat, ok := i.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) || syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil, errors.New("modelpack: staging directory not owned or already locked")
	}
	return f, nil
}

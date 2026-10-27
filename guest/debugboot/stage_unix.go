//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"bytes"
	"embed"
	"io"
	"os"
	"syscall"
)

//go:embed nib-debug-boot.service nib-debug-boot.target nib-debug-network-failure.service 90-nib-debug-network-failure.conf
var units embed.FS

func ownedDirectory(root *os.Root) bool {
	info, err := root.Stat(".")
	return err == nil && info.IsDir() && info.Sys().(*syscall.Stat_t).Uid == uint32(os.Geteuid()) && info.Mode().Perm()&0022 == 0
}

func newFileAt(root *os.Root, name string, raw []byte, mode os.FileMode) error {
	f, err := root.OpenFile(name, os.O_RDWR|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, mode)
	if err != nil {
		return errRejected
	}
	defer f.Close()
	if err := f.Chmod(mode); err != nil {
		return errRejected
	}
	if n, err := f.Write(raw); err != nil || n != len(raw) {
		return errRejected
	}
	if _, err := f.Seek(0, 0); err != nil {
		return errRejected
	}
	check, err := io.ReadAll(io.LimitReader(f, int64(len(raw))+1))
	if err != nil || !bytes.Equal(raw, check) || f.Sync() != nil {
		return errRejected
	}
	return nil
}

func childDirectory(root *os.Root, name string) (*os.Root, error) {
	if err := root.Mkdir(name, 0755); err != nil && !os.IsExist(err) {
		return nil, errRejected
	}
	before, err := root.Lstat(name)
	if err != nil || !before.IsDir() {
		return nil, errRejected
	}
	child, err := root.OpenRoot(name)
	if err != nil {
		return nil, errRejected
	}
	after, err := child.Stat(".")
	if err != nil || !os.SameFile(before, after) || !ownedDirectory(child) {
		child.Close()
		return nil, errRejected
	}
	return child, nil
}

// The initrd's /run is carried across switch-root by systemd v255.4. Stage only
// fixed files, never overwrite existing units/binaries, and keep failure fatal
// to initrd-switch-root.service through the debug-only Requires drop-in.
func stageAt(run *os.Root, executable []byte, network bool) error {
	service, err := units.ReadFile("nib-debug-boot.service")
	if err != nil {
		return errRejected
	}
	return stageServiceAt(run, executable, network, service)
}

func stageServiceAt(run *os.Root, executable []byte, network bool, service []byte) error {
	if !ownedDirectory(run) || len(executable) == 0 || len(executable) > 8<<20 || len(service) == 0 || len(service) > 16384 {
		return errRejected
	}
	systemd, err := childDirectory(run, "systemd")
	if err != nil {
		return err
	}
	defer systemd.Close()
	system, err := childDirectory(systemd, "system")
	if err != nil {
		return err
	}
	defer system.Close()
	if err := newFileAt(run, "nib-debug-boot", executable, 0500); err != nil {
		return err
	}
	for _, name := range []string{"nib-debug-boot.service", "nib-debug-boot.target"} {
		raw, err := units.ReadFile(name)
		if name == "nib-debug-boot.service" {
			raw = service
		}
		if err != nil || newFileAt(system, name, raw, 0444) != nil {
			return errRejected
		}
	}
	if network {
		raw, err := units.ReadFile("nib-debug-network-failure.service")
		if err != nil || newFileAt(system, "nib-debug-network-failure.service", raw, 0444) != nil {
			return errRejected
		}
		dropin, err := childDirectory(system, "nib-network.service.d")
		if err != nil {
			return err
		}
		defer dropin.Close()
		raw, err = units.ReadFile("90-nib-debug-network-failure.conf")
		if err != nil || newFileAt(dropin, "90-nib-debug-network-failure.conf", raw, 0444) != nil {
			return errRejected
		}
	}
	return nil
}

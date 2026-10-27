//go:build (linux || darwin) && nibdebugtopology && !nibfake

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestTopologyStageFixedUnitAndObserver(t *testing.T) {
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err := stageTopologyAt(root, []byte("synthetic executable")); err != nil {
		t.Fatal(err)
	}
	service, _ := topologyUnits.ReadFile("nib-debug-topology.service")
	got, err := root.ReadFile("systemd/system/nib-debug-boot.service")
	if err != nil || !bytes.Equal(got, service) {
		t.Fatal("wrong unit", err)
	}
	for path, name := range map[string]string{
		"systemd/system/nib-debug-boot.target":                                   "nib-debug-boot.target",
		"systemd/system/nib-debug-network-failure.service":                       "nib-debug-network-failure.service",
		"systemd/system/nib-network.service.d/90-nib-debug-network-failure.conf": "90-nib-debug-network-failure.conf",
	} {
		got, err := root.ReadFile(path)
		want, _ := units.ReadFile(name)
		if err != nil || !bytes.Equal(got, want) {
			t.Fatal("original shutdown guard changed", path, err)
		}
	}
	for _, line := range []string{"CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_ADMIN\n", "RestrictAddressFamilies=AF_UNIX AF_NETLINK\n",
		"Requires=systemd-udevd.service systemd-udev-trigger.service nib-network.service\n", "BindsTo=nib-network.service\n",
		"NoNewPrivileges=yes\n", "TimeoutStartSec=150\n", "TimeoutStopSec=2\n", "MemoryMax=64M\n", "TasksMax=32\n"} {
		if bytes.Count(service, []byte(line)) != 1 {
			t.Fatal("unit contract drift", line)
		}
	}
	if stageTopologyAt(root, []byte("replacement")) == nil {
		t.Fatal("overwrite accepted")
	}
	got, err = root.ReadFile("nib-debug-boot")
	if err != nil || string(got) != "synthetic executable" {
		t.Fatal("original replaced", err)
	}
}

func TestTopologyStageRejectsUnsafeParentsAndCollisions(t *testing.T) {
	for _, bad := range []string{"parent-link", "parent-writable", "binary-link", "unit", "observer", "empty", "oversize"} {
		t.Run(bad, func(t *testing.T) {
			dir, outside := t.TempDir(), t.TempDir()
			parent := filepath.Join(dir, "systemd/system")
			if err := os.MkdirAll(parent, 0755); err != nil {
				t.Fatal(err)
			}
			var err error
			raw := []byte("synthetic")
			switch bad {
			case "parent-link":
				err = os.Symlink(outside, filepath.Join(parent, "nib-network.service.d"))
			case "parent-writable":
				err = os.Chmod(parent, 0777)
			case "binary-link":
				err = os.Symlink(filepath.Join(outside, "victim"), filepath.Join(dir, "nib-debug-boot"))
			case "unit", "observer":
				name := "nib-debug-boot.service"
				if bad == "observer" {
					name = "nib-debug-network-failure.service"
				}
				err = os.WriteFile(filepath.Join(parent, name), []byte("original"), 0444)
			case "empty":
				raw = nil
			case "oversize":
				raw = make([]byte, (8<<20)+1)
			}
			if err != nil {
				t.Fatal(err)
			}
			root, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer root.Close()
			if stageTopologyAt(root, raw) == nil {
				t.Fatal("unsafe stage accepted")
			}
			entries, err := os.ReadDir(outside)
			if err != nil || len(entries) != 0 {
				t.Fatal("outside write", err)
			}
		})
	}
}

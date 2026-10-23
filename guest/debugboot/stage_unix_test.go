//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInitrdCarryIsExclusiveAndFixed(t *testing.T) {
	dir := t.TempDir()
	root, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err := stageAt(root, []byte("synthetic executable"), false); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]os.FileMode{"nib-debug-boot": 0500,
		"systemd/system/nib-debug-boot.service": 0444, "systemd/system/nib-debug-boot.target": 0444} {
		info, err := root.Lstat(name)
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != want {
			t.Fatal("unexpected staged mode", name, err)
		}
	}
	if _, err := root.Lstat("systemd/system/nib-network.service.d"); !os.IsNotExist(err) {
		t.Fatal("default probe acquired network override", err)
	}
	if err := stageAt(root, []byte("replacement"), false); err == nil {
		t.Fatal("collision accepted")
	}
	raw, err := os.ReadFile(filepath.Join(dir, "nib-debug-boot"))
	if err != nil || string(raw) != "synthetic executable" {
		t.Fatal("collision overwrote original", err)
	}
}

func TestInitrdCarryRejectsLinksAndWritableParents(t *testing.T) {
	for _, kind := range []string{"parent-link", "parent-writable", "leaf-link"} {
		t.Run(kind, func(t *testing.T) {
			dir, outside := t.TempDir(), t.TempDir()
			var err error
			switch kind {
			case "parent-link":
				err = os.Symlink(outside, filepath.Join(dir, "systemd"))
			case "parent-writable":
				err = os.Mkdir(filepath.Join(dir, "systemd"), 0755)
				if err == nil {
					err = os.Chmod(filepath.Join(dir, "systemd"), 0777)
				}
			case "leaf-link":
				err = os.Symlink(filepath.Join(outside, "victim"), filepath.Join(dir, "nib-debug-boot"))
			}
			if err != nil {
				t.Fatal(err)
			}
			root, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer root.Close()
			if stageAt(root, []byte("synthetic executable"), true) == nil {
				t.Fatal("unsafe destination accepted")
			}
			entries, err := os.ReadDir(outside)
			if err != nil || len(entries) != 0 {
				t.Fatal("outside destination modified", err)
			}
		})
	}
}

func TestNetworkObserverStagingIsExplicitAndExclusive(t *testing.T) {
	for _, collision := range []string{"", "leaf", "parent-link", "parent-writable"} {
		t.Run(collision, func(t *testing.T) {
			dir, outside := t.TempDir(), t.TempDir()
			parent := filepath.Join(dir, "systemd/system/nib-network.service.d")
			if err := os.MkdirAll(filepath.Dir(parent), 0755); err != nil {
				t.Fatal(err)
			}
			switch collision {
			case "parent-link":
				if err := os.Symlink(outside, parent); err != nil {
					t.Fatal(err)
				}
			case "parent-writable":
				if err := os.Mkdir(parent, 0755); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(parent, 0777); err != nil {
					t.Fatal(err)
				}
			case "leaf":
				if err := os.WriteFile(filepath.Join(dir, "systemd/system/nib-debug-network-failure.service"), []byte("original"), 0444); err != nil {
					t.Fatal(err)
				}
			}
			root, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer root.Close()
			if err := stageAt(root, []byte("synthetic executable"), true); (err != nil) != (collision != "") {
				t.Fatal("incorrect staging acceptance", err)
			}
			if entries, err := os.ReadDir(outside); err != nil || len(entries) != 0 {
				t.Fatal("outside destination modified", err)
			}
			if collision == "" {
				for path, name := range map[string]string{
					"systemd/system/nib-debug-network-failure.service":                       "nib-debug-network-failure.service",
					"systemd/system/nib-network.service.d/90-nib-debug-network-failure.conf": "90-nib-debug-network-failure.conf"} {
					got, err := root.ReadFile(path)
					want, _ := units.ReadFile(name)
					if err != nil || string(got) != string(want) {
						t.Fatal("observer source changed", err)
					}
				}
			}
		})
	}
}

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
	if err := stageAt(root, []byte("synthetic executable")); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]os.FileMode{"nib-debug-boot": 0500,
		"systemd/system/nib-debug-boot.service": 0444, "systemd/system/nib-debug-boot.target": 0444} {
		info, err := root.Lstat(name)
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != want {
			t.Fatal("unexpected staged mode", name, err)
		}
	}
	if err := stageAt(root, []byte("replacement")); err == nil {
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
			if stageAt(root, []byte("synthetic executable")) == nil {
				t.Fatal("unsafe destination accepted")
			}
			entries, err := os.ReadDir(outside)
			if err != nil || len(entries) != 0 {
				t.Fatal("outside destination modified", err)
			}
		})
	}
}

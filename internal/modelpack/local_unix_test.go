//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func TestLocalFIFOAndLinkSwapCannotBlock(t *testing.T) {
	for _, kind := range []string{"fifo", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			p, dir := localFixture(t)
			pack, manifest, err := Load(p)
			if err != nil {
				t.Fatal(err)
			}
			files, err := config.ParseFileManifest(manifest, pack.TotalBytes)
			if err != nil {
				t.Fatal(err)
			}
			r, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer r.Close()
			f := files[0]
			before, err := r.Stat(f.Path)
			if err != nil {
				t.Fatal(err)
			}
			file := filepath.Join(dir, f.Path)
			if err := os.Rename(file, file+".old"); err != nil {
				t.Fatal(err)
			}
			if kind == "fifo" {
				err = syscall.Mkfifo(file, 0600)
			} else {
				err = os.Symlink(file+".old", file)
			}
			if err != nil {
				t.Fatal(err)
			}
			if verifyLocalFile(context.Background(), r, f, before, make([]byte, 16)) == nil {
				t.Fatal("unsafe swap accepted")
			}
			if _, err := CheckLocal(context.Background(), p, dir); err == nil {
				t.Fatal("unsafe inventory accepted")
			}
		})
	}
}

//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"context"
	"debug/elf"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDebugCommandIsExplicitAndStatic(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	env := append(os.Environ(), "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0", "GOFLAGS=", "GOWORK=off", "GOTOOLCHAIN=local",
		"GOMODCACHE="+t.TempDir(), "GOPROXY=off", "GOSUMDB=off")
	plain := exec.CommandContext(ctx, "go", "list", "./cmd/nib-debug-boot")
	plain.Env = env
	if out, err := plain.CombinedOutput(); err == nil || !strings.Contains(string(out), "build constraints exclude all Go files") {
		t.Fatal("debug executable entered default build", err)
	}
	path := filepath.Join(t.TempDir(), "nib-debug-boot")
	build := exec.CommandContext(ctx, "go", "build", "-trimpath", "-buildvcs=false", "-tags=nibdebugboot", "-o", path, "./cmd/nib-debug-boot")
	build.Env = env
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("debug cross-build: %v\n%s", err, out)
	}
	image, err := elf.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer image.Close()
	if image.Class != elf.ELFCLASS64 || image.Machine != elf.EM_X86_64 {
		t.Fatal("unexpected executable architecture")
	}
	for _, program := range image.Progs {
		if program.Type == elf.PT_INTERP || program.Type == elf.PT_DYNAMIC {
			t.Fatal("debug probe requires a dynamic runtime")
		}
	}
	list := exec.CommandContext(ctx, "go", "list", "-deps", "-tags=nibdebugboot", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", "./cmd/nib-debug-boot")
	list.Env = env
	out, err := list.CombinedOutput()
	if err != nil || strings.Join(strings.Fields(string(out)), "\n") != "github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot\n"+
		"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot/cmd/nib-debug-boot" {
		t.Fatal("non-standard guest dependency", err)
	}
}

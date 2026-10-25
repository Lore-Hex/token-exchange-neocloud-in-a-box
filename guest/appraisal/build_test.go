//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package appraisal

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

func TestCollectorIsExplicitStaticAndStandardLibraryOnly(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	env := append(os.Environ(), "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0", "GOFLAGS=", "GOWORK=off", "GOTOOLCHAIN=local",
		"GOMODCACHE="+t.TempDir(), "GOPROXY=off", "GOSUMDB=off")
	plain := exec.CommandContext(ctx, "go", "list", "./cmd/nib-h100-appraisal")
	plain.Env = env
	if out, err := plain.CombinedOutput(); err == nil || !strings.Contains(string(out), "build constraints exclude all Go files") {
		t.Fatal("collector executable entered default build", err)
	}
	path := filepath.Join(t.TempDir(), "nib-h100-appraisal")
	build := exec.CommandContext(ctx, "go", "build", "-trimpath", "-buildvcs=false", "-tags=nibh100appraisal", "-o", path, "./cmd/nib-h100-appraisal")
	build.Env = env
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("collector cross-build: %v\n%s", err, out)
	}
	image, err := elf.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer image.Close()
	if image.Class != elf.ELFCLASS64 || image.Machine != elf.EM_X86_64 {
		t.Fatal("unexpected collector architecture")
	}
	for _, program := range image.Progs {
		if program.Type == elf.PT_INTERP || program.Type == elf.PT_DYNAMIC {
			t.Fatal("collector needs a dynamic runtime")
		}
	}
	list := exec.CommandContext(ctx, "go", "list", "-deps", "-tags=nibh100appraisal", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", "./cmd/nib-h100-appraisal")
	list.Env = env
	out, err := list.CombinedOutput()
	if err != nil || strings.Join(strings.Fields(string(out)), "\n") != "github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables\n"+
		"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/appraisal\n"+
		"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/appraisal/cmd/nib-h100-appraisal" {
		t.Fatal("non-standard collector dependency", err)
	}
}

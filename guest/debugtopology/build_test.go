//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package debugtopology

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

func TestExplicitStaticCommandAndUnchangedDefaultImportClosure(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	env := append(os.Environ(), "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0", "GOFLAGS=", "GOWORK=off", "GOTOOLCHAIN=local",
		"GOMODCACHE="+t.TempDir(), "GOPROXY=off", "GOSUMDB=off")
	run := func(args ...string) ([]byte, error) {
		c := exec.CommandContext(ctx, "go", args...)
		c.Env = env
		return c.CombinedOutput()
	}
	command := "../debugboot/cmd/nib-debug-topology"
	for _, tag := range []string{"", "nibdebugboot", "nibdebugtopology,nibfake"} {
		out, err := run("list", "-tags="+tag, command)
		if err == nil || !strings.Contains(string(out), "build constraints exclude all Go files") {
			t.Fatal("new command activated", tag, err, string(out))
		}
	}
	path := filepath.Join(t.TempDir(), "topology")
	if out, err := run("build", "-trimpath", "-buildvcs=false", "-tags=nibdebugtopology", "-o", path, command); err != nil {
		t.Fatalf("cross-build %v: %s", err, out)
	}
	image, err := elf.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer image.Close()
	if image.Class != elf.ELFCLASS64 || image.Machine != elf.EM_X86_64 {
		t.Fatal("wrong arch")
	}
	for _, p := range image.Progs {
		if p.Type == elf.PT_INTERP || p.Type == elf.PT_DYNAMIC {
			t.Fatal("dynamic dependency")
		}
	}
	out, err := run("list", "-deps", "-tags=nibdebugtopology", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", command)
	if err != nil || strings.Join(strings.Fields(string(out)), "\n") != "github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugtopology\n"+
		"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot\n"+
		"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot/cmd/nib-debug-topology" {
		t.Fatal("unreviewed dependency", err, string(out))
	}
	for _, target := range []string{"../cmd/...", "../debugboot/cmd/nib-debug-boot"} {
		out, err := run("list", "-deps", "-tags=nibdebugboot", "-f", "{{.ImportPath}}", target)
		if err != nil || strings.Contains(string(out), "/debugtopology") {
			t.Fatal("default helper gained topology dependency", err, string(out))
		}
	}
}

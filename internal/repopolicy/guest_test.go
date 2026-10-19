// SPDX-License-Identifier: Apache-2.0
package repopolicy

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const module = "github.com/Lore-Hex/token-exchange-neocloud-in-a-box"

type goPackage struct {
	ImportPath string
	Standard   bool
}

func rejectExternalPackages(data []byte, ownModule string) error {
	d := json.NewDecoder(bytes.NewReader(data))
	for {
		var p goPackage
		if err := d.Decode(&p); err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		if !p.Standard && p.ImportPath != ownModule && !strings.HasPrefix(p.ImportPath, ownModule+"/") {
			return fmt.Errorf("guest dependency is not standard library: %s", p.ImportPath)
		}
	}
}

func TestGuestDependenciesAreStandardLibraryOnly(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, cgo := range []string{"0", "1"} {
		for _, tags := range []string{"", "nibfake"} {
			cmd := exec.Command("go", "list", "-deps", "-test", "-json", "-tags", tags, "./guest/...")
			cmd.Dir = root
			cmd.Env = append(os.Environ(), "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED="+cgo)
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			data, err := cmd.Output()
			if err != nil {
				t.Fatalf("go list failed: %v: %s", err, stderr.String())
			}
			if len(data) == 0 {
				t.Fatal("guest boundary missing; refusing a vacuous dependency check")
			}
			if err := rejectExternalPackages(data, module); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func TestDependencyGuardPositiveControl(t *testing.T) {
	for _, foreign := range []string{"example.org/thirdparty", module + "-lookalike/guest"} {
		data, err := json.Marshal(goPackage{ImportPath: foreign})
		if err != nil {
			t.Fatal(err)
		}
		if rejectExternalPackages(data, module) == nil {
			t.Fatalf("allowed %s", foreign)
		}
	}
	if rejectExternalPackages([]byte(`{"ImportPath":"net/http","Standard":true}`), module) != nil {
		t.Fatal("rejected standard library")
	}
	if rejectExternalPackages([]byte("invalid"), module) == nil {
		t.Fatal("accepted invalid dependency output")
	}
}

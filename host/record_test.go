// SPDX-License-Identifier: Apache-2.0

package host_test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func TestProbeRecordsUseExistingEvidenceSchema(t *testing.T) {
	dir := t.TempDir()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "python3", "-I", "../scripts/test_probe_evidence.py", "--schema-fixtures", dir)
	cmd.WaitDelay = time.Second
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("synthetic recorder fixtures failed: %v\n%s", err, output)
	}
	for _, name := range []string{"pass", "fail"} {
		data, err := os.ReadFile(filepath.Join(dir, name+".json"))
		if err != nil {
			t.Fatal(err)
		}
		if err := config.ValidateEvidence(data); err != nil {
			t.Fatalf("%s recorder output violates authoritative schema: %v", name, err)
		}
	}
}

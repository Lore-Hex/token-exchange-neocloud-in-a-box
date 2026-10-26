// SPDX-License-Identifier: Apache-2.0
package nftables

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestH100OfflinePlanUsesUnchangedPolicy(t *testing.T) {
	// Shared with the Python preparer's byte-exact render test; documentation
	// addresses are synthetic. No kernel command or connection occurs here.
	raw, err := os.ReadFile("testdata/h100-static-plan.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		NetworkConfig string `json:"network_config"`
	}
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	destinations, err := Config([]byte(fixture.NetworkConfig))
	if err != nil || len(destinations) != 1 || destinations[0] != "198.51.100.10" {
		t.Fatalf("offline configuration rejected: %v", err)
	}
	rendered, err := Render(destinations)
	if err != nil {
		t.Fatal(err)
	}
	empty, err := Render(nil)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(rendered), "elements =") != 1 ||
		strings.Replace(string(rendered), "elements = { 198.51.100.10 };", "", 1) != string(empty) {
		t.Fatal("plan changes more than the single IPv4 destination set")
	}
	// The same config parser still rejects endpoint/port/DNS escape hatches.
	for _, extra := range []string{`"dns":true`, `"port":80`, `"url":"https://example.invalid"`} {
		bad := strings.Replace(fixture.NetworkConfig, "{", "{"+extra+",", 1)
		if _, err := Config([]byte(bad)); err == nil {
			t.Fatal("extra policy capability accepted")
		}
	}
}

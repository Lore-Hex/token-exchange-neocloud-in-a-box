// SPDX-License-Identifier: Apache-2.0
package systemd_test

import (
	"bufio"
	"os"
	"strings"
	"testing"
)

// This checks our small declarative contract, not systemd runtime behavior.
func readUnit(t *testing.T, name string) map[string]string {
	t.Helper()
	b, err := os.ReadFile(name)
	if err != nil {
		t.Fatal(err)
	}
	values := map[string]string{}
	section := ""
	s := bufio.NewScanner(strings.NewReader(string(b)))
	for s.Scan() {
		line := strings.TrimSpace(s.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = line[1 : len(line)-1]
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok || section == "" {
			t.Fatalf("invalid unit line %q", line)
		}
		key = section + "." + key
		if _, exists := values[key]; exists {
			t.Fatalf("duplicate unit directive %s", key)
		}
		values[key] = value
	}
	if err := s.Err(); err != nil {
		t.Fatal(err)
	}
	return values
}

func require(t *testing.T, values, want map[string]string) {
	t.Helper()
	for key, value := range want {
		if values[key] != value {
			t.Errorf("%s = %q, want %q", key, values[key], value)
		}
	}
}

func TestGateOrderingAndHardening(t *testing.T) {
	for _, name := range []string{"network", "gpu-gate", "weights", "engine"} {
		t.Run(name, func(t *testing.T) {
			v := readUnit(t, "nib-"+name+".service")
			require(t, v, map[string]string{
				"Unit.OnFailure":    "nib-failclosed.target",
				"Service.ExecStart": "/usr/libexec/nib-" + name + " --config /etc/nib/guest.json",
				"Service.User":      "nib", "Service.Group": "nib", "Service.NoNewPrivileges": "yes",
				"Service.ProtectSystem": "strict", "Service.ProtectHome": "yes", "Service.PrivateTmp": "yes",
				"Service.UMask": "0077", "Service.LimitCORE": "0",
				"Service.StandardOutput": "journal", "Service.StandardError": "journal",
			})
			if name == "engine" {
				gates := "nib-network.service nib-gpu-gate.service nib-weights.service"
				require(t, v, map[string]string{"Unit.Requires": gates, "Unit.BindsTo": gates, "Unit.After": gates,
					"Service.Type": "exec", "Service.Restart": "no", "Service.KillMode": "control-group",
					"Service.TimeoutStopSec": "5", "Service.RestrictAddressFamilies": "AF_INET AF_UNIX"})
			} else {
				require(t, v, map[string]string{"Service.Type": "oneshot", "Service.RemainAfterExit": "yes", "Service.TimeoutStartSec": "10"})
				if name != "network" {
					require(t, v, map[string]string{"Unit.Requires": "nib-network.service", "Unit.After": "nib-network.service", "Unit.Before": "nib-engine.service"})
				} else {
					require(t, v, map[string]string{"Unit.Before": "nib-gpu-gate.service nib-weights.service nib-engine.service"})
				}
			}
			for key := range v {
				if strings.HasPrefix(key, "Install.") || strings.HasPrefix(key, "Unit.Condition") || key == "Service.ExecCondition" || key == "Service.SuccessExitStatus" {
					t.Errorf("automatic installation or skipped failure path: %s", key)
				}
			}
		})
	}
}

func TestNodeAndFailclosedTargets(t *testing.T) {
	require(t, readUnit(t, "nib-node.target"), map[string]string{
		"Unit.Requires": "nib-engine.service", "Unit.BindsTo": "nib-engine.service", "Unit.After": "nib-engine.service", "Unit.OnFailure": "nib-failclosed.target",
	})
	require(t, readUnit(t, "nib-failclosed.target"), map[string]string{
		"Unit.DefaultDependencies": "no", "Unit.Requires": "poweroff.target", "Unit.Before": "poweroff.target",
		"Unit.After": "nib-node.target nib-engine.service", "Unit.Conflicts": "nib-node.target nib-engine.service",
	})
}

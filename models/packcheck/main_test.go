// SPDX-License-Identifier: Apache-2.0
package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"go.yaml.in/yaml/v3"
)

func TestNoOverwrite(t *testing.T) {
	p := filepath.Join(t.TempDir(), "artifact")
	if err := writeExclusive(p, []byte("first")); err != nil {
		t.Fatal(err)
	}
	if err := writeExclusive(p, []byte("second")); err == nil {
		t.Fatal("overwrote evidence")
	}
	b, err := os.ReadFile(p)
	if err != nil || string(b) != "first" {
		t.Fatal("changed evidence")
	}
}

func TestDraftEngineProfilesStayPrivateAndNonDeployable(t *testing.T) {
	for _, p := range packs {
		t.Run(p.repo, func(t *testing.T) {
			b, err := os.ReadFile(filepath.Join("../..", p.dir, "pack.yaml"))
			if err != nil {
				t.Fatal(err)
			}
			if err := config.ValidateModelPack(b); err != nil {
				t.Fatal(err)
			}
			if config.ValidateDeployableModelPack(b) == nil {
				t.Fatal("metadata upgrade made draft deployable")
			}
			var pack struct {
				Profiles map[string]struct {
					Image string            `yaml:"image"`
					Args  []string          `yaml:"args"`
					Env   map[string]string `yaml:"env"`
				} `yaml:"engine_profiles"`
			}
			if err := yaml.Unmarshal(b, &pack); err != nil {
				t.Fatal(err)
			}
			profile, ok := pack.Profiles["b200x8-gcp-plain"]
			if !ok || len(pack.Profiles) != 1 || profile.Image != upstreamImage {
				t.Fatal("unreviewed image or confidential profile")
			}
			for _, flag := range []string{"--no-enable-log-requests", "--no-enable-log-outputs", "--disable-uvicorn-access-log"} {
				if !slices.Contains(profile.Args, flag) {
					t.Errorf("missing privacy flag %s", flag)
				}
			}
			for _, flag := range []string{"--trust-remote-code", "--enable-log-requests", "--enable-log-outputs"} {
				if slices.Contains(profile.Args, flag) {
					t.Errorf("unsafe flag %s", flag)
				}
			}
			for flag, want := range map[string]string{"--host": "127.0.0.1", "--model": "/model", "--max-model-len": "1048576"} {
				i := slices.Index(profile.Args, flag)
				if i < 0 || i+1 == len(profile.Args) || profile.Args[i+1] != want {
					t.Errorf("profile changed %s", flag)
				}
			}
			if p.repo == "deepseek-ai/DeepSeek-V4.1-Flash-1017" {
				i := slices.Index(profile.Args, "--engram-config")
				if i < 0 || i+1 == len(profile.Args) || profile.Args[i+1] != `{"cpu_offload":false}` {
					t.Fatal("GPU-resident Engram setting missing")
				}
			}
			if profile.Env["VLLM_NO_USAGE_STATS"] != "1" || profile.Env["VLLM_DO_NOT_TRACK"] != "1" || profile.Env["VLLM_USE_RUST_FRONTEND"] != "0" {
				t.Fatal("telemetry/frontend settings changed")
			}
			if _, ok := profile.Env["VLLM_DISABLE_UVA"]; ok {
				t.Fatal("unsupported global UVA flag added")
			}
		})
	}
}
func TestRejectArbitraryArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"SECRET_PATH"}, {"generate", "SECRET_PATH"}, {"--out", "SECRET_PATH"}} {
		if err := run(args); err == nil || strings.Contains(err.Error(), "SECRET_PATH") {
			t.Fatal("arguments accepted or leaked")
		}
	}
}

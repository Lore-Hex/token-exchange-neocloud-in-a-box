// SPDX-License-Identifier: Apache-2.0

package config

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const packPath = "models/fixture-model/1/pack.yaml"
const profilePath = "profiles/b200x2-tdx.yaml"
const manifestPath = "models/fixture-model/1/files.sha256"

func write(t *testing.T, root, name string, data []byte) {
	t.Helper()
	file := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(file), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func repository(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for path, name := range map[string]string{packPath: "model-pack.json", profilePath: "hardware-profile.json", manifestPath: "files.sha256", "release.json": "release.json", "evidence/p1/synthetic/run.json": "evidence.json"} {
		write(t, root, path, fixture(t, "valid/"+name))
	}
	return root
}

func edit(t *testing.T, root, name string, f func(map[string]any)) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, name))
	if err != nil {
		t.Fatal(err)
	}
	var v map[string]any
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		t.Fatal(err)
	}
	f(v)
	data, err = json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	write(t, root, name, data)
}

func remove(t *testing.T, root, name string) {
	t.Helper()
	if err := os.Remove(filepath.Join(root, name)); err != nil {
		t.Fatal(err)
	}
}

func TestRepository(t *testing.T) {
	root := repository(t)
	if err := ValidateRepository(root); err != nil {
		t.Fatal(err)
	}
	if err := ValidateRepository(t.TempDir()); err != nil {
		t.Fatal("empty skeleton", err)
	}
	if err := ValidateRepository(filepath.Join(root, "missing")); err == nil {
		t.Fatal("missing root accepted")
	}
	if err := ValidateRepository(filepath.Join(root, "release.json")); err == nil {
		t.Fatal("file root accepted")
	}
	for _, path := range []string{"tests/bad/release.json", "internal/config/testdata/evidence/run.json", "build/release.json", "vendor/profiles/a.yaml", ".git/release.json", "README.md", "catalog/pricing.yaml"} {
		write(t, root, path, []byte("not config"))
	}
	if err := ValidateRepository(root); err != nil {
		t.Fatal("scanned excluded file", err)
	}
}

func TestRepositoryFailures(t *testing.T) {
	cases := map[string]func(*testing.T, string){
		"missing-manifest": func(t *testing.T, root string) { remove(t, root, manifestPath) },
		"manifest-total": func(t *testing.T, root string) {
			edit(t, root, packPath, func(v map[string]any) { v["total_bytes"] = 4 })
		},
		"invalid-pack": func(t *testing.T, root string) {
			edit(t, root, packPath, func(v map[string]any) { v["hf_revision"] = "main" })
		},
		"pack-directory": func(t *testing.T, root string) {
			edit(t, root, packPath, func(v map[string]any) { v["id"] = "wrong@1" })
		},
		"duplicate-pack": func(t *testing.T, root string) {
			write(t, root, "models/duplicate/1/pack.yaml", fixture(t, "valid/model-pack.json"))
		},
		"profile-filename": func(t *testing.T, root string) {
			edit(t, root, profilePath, func(v map[string]any) { v["id"] = "wrong" })
		},
		"duplicate-profile": func(t *testing.T, root string) {
			write(t, root, "profiles/b200x2-tdx.yml", fixture(t, "valid/hardware-profile.json"))
		},
		"invalid-evidence": func(t *testing.T, root string) {
			write(t, root, "evidence/p0/new/run.json", fixture(t, "invalid/evidence/fake-hardware.json"))
		},
		"nested-release": func(t *testing.T, root string) {
			write(t, root, "releases/test/release.json", fixture(t, "invalid/release/debug.json"))
		},
		"missing-pack-ref":    func(t *testing.T, root string) { remove(t, root, packPath) },
		"missing-profile-ref": func(t *testing.T, root string) { remove(t, root, profilePath) },
		"draft-pack":          func(t *testing.T, root string) { write(t, root, packPath, fixture(t, "valid/model-pack-draft.json")) },
		"plain-profile": func(t *testing.T, root string) {
			edit(t, root, profilePath, func(v map[string]any) { v["cc_mode"] = "none" })
		},
		"debug-release": func(t *testing.T, root string) {
			edit(t, root, "release.json", func(v map[string]any) { v["variant"] = "debug" })
		},
		"weight-pin-mismatch": func(t *testing.T, root string) {
			edit(t, root, "release.json", func(v map[string]any) { v["model_pack"].(map[string]any)["verity_root_hash"] = strings.Repeat("d", 64) })
		},
		"engine-pin-mismatch": func(t *testing.T, root string) {
			edit(t, root, "release.json", func(v map[string]any) {
				v["images"].(map[string]any)["engine"] = "example.invalid/fixture@sha256:" + strings.Repeat("d", 64)
			})
		},
		"engine-profile-missing": func(t *testing.T, root string) {
			edit(t, root, packPath, func(v map[string]any) {
				p := v["engine_profiles"].(map[string]any)
				p["other"] = p["b200x2-tdx"]
				delete(p, "b200x2-tdx")
			})
		},
		"nonregular-manifest": func(t *testing.T, root string) {
			remove(t, root, manifestPath)
			if err := os.Mkdir(filepath.Join(root, manifestPath), 0700); err != nil {
				t.Fatal(err)
			}
		},
		"oversize-config": func(t *testing.T, root string) {
			write(t, root, packPath, bytes.Repeat([]byte(" "), MaxDocumentBytes+1))
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			root := repository(t)
			mutate(t, root)
			if err := ValidateRepository(root); err == nil {
				t.Fatal("invalid repository accepted")
			}
		})
	}
}

func TestDraftRepositoryAndYMLExtensions(t *testing.T) {
	root := repository(t)
	remove(t, root, "release.json")
	write(t, root, packPath, fixture(t, "valid/model-pack-draft.json"))
	if err := os.Rename(filepath.Join(root, packPath), filepath.Join(root, "models/fixture-model/1/pack.yml")); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(root, profilePath), filepath.Join(root, "profiles/b200x2-tdx.yml")); err != nil {
		t.Fatal(err)
	}
	if err := ValidateRepository(root); err != nil {
		t.Fatal(err)
	}
}

func TestRepositoryExactTotals(t *testing.T) {
	for _, literal := range []string{"1e6", "1.0", "3e0", "3.0"} {
		t.Run(literal, func(t *testing.T) {
			root := repository(t)
			data := bytes.Replace(fixture(t, "valid/model-pack.json"), []byte(`"total_bytes": 3`), []byte(`"total_bytes": `+literal), 1)
			write(t, root, packPath, data)
			write(t, root, manifestPath, []byte(strings.Repeat("a", 64)+"  0  empty.json\n"))
			if err := ValidateRepository(root); err == nil {
				t.Fatal("scientific notation mistaken for zero")
			}
			if literal == "3e0" || literal == "3.0" {
				write(t, root, manifestPath, fixture(t, "valid/files.sha256"))
				if err := ValidateRepository(root); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
}

func TestRepositorySymlinks(t *testing.T) {
	for _, name := range []string{packPath, manifestPath, profilePath, "release.json"} {
		t.Run(name, func(t *testing.T) {
			root := repository(t)
			outside := filepath.Join(t.TempDir(), "target")
			data, err := os.ReadFile(filepath.Join(root, name))
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(outside, data, 0600); err != nil {
				t.Fatal(err)
			}
			remove(t, root, name)
			if err := os.Symlink(outside, filepath.Join(root, name)); err != nil {
				t.Fatal(err)
			}
			if err := ValidateRepository(root); err == nil {
				t.Fatal("symlink accepted")
			}
		})
	}
	root := t.TempDir()
	if err := os.Symlink(t.TempDir(), filepath.Join(root, "models")); err != nil {
		t.Fatal(err)
	}
	if err := ValidateRepository(root); err == nil {
		t.Fatal("symlinked artifact directory accepted")
	}
}

func TestDeepSeekConfidentialSelection(t *testing.T) {
	root := repository(t)
	write(t, root, packPath, fixture(t, "valid/model-pack-deepseek.json"))
	edit(t, root, packPath, func(v map[string]any) {
		profiles := v["engine_profiles"].(map[string]any)
		plain := make(map[string]any)
		for k, value := range profiles["b200x2-tdx"].(map[string]any) {
			plain[k] = value
		}
		plain["env"] = map[string]any{"VLLM_DISABLE_UVA": "0"}
		profiles["b200x2-gcp-plain"] = plain
	})
	if err := ValidateRepository(root); err != nil {
		t.Fatal("plain comparison prevented confidential release", err)
	}
	// A nonstandard profile name must not evade the hardware-context rule.
	edit(t, root, packPath, func(v map[string]any) {
		profiles := v["engine_profiles"].(map[string]any)
		profiles["custom"] = profiles["b200x2-tdx"]
		delete(profiles, "b200x2-tdx")
	})
	edit(t, root, profilePath, func(v map[string]any) { v["id"] = "custom" })
	if err := os.Rename(filepath.Join(root, profilePath), filepath.Join(root, "profiles/custom.yaml")); err != nil {
		t.Fatal(err)
	}
	edit(t, root, "release.json", func(v map[string]any) {
		profiles := v["profiles"].(map[string]any)
		profiles["custom"] = profiles["b200x2-tdx"]
		delete(profiles, "b200x2-tdx")
	})
	if err := ValidateRepository(root); err != nil {
		t.Fatal(err)
	}
	edit(t, root, packPath, func(v map[string]any) {
		v["engine_profiles"].(map[string]any)["custom"].(map[string]any)["env"] = map[string]any{"VLLM_DISABLE_UVA": "0"}
	})
	if err := ValidateRepository(root); err == nil {
		t.Fatal("custom confidential profile evaded UVA rule")
	}
}

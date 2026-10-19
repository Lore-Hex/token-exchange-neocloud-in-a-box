// SPDX-License-Identifier: Apache-2.0

package config

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestValidFixtures(t *testing.T) {
	files, err := filepath.Glob("testdata/valid/*.json")
	if err != nil || len(files) == 0 {
		t.Fatal("missing fixtures", err)
	}
	for _, file := range files {
		t.Run(filepath.Base(file), func(t *testing.T) {
			var kind Kind
			for _, candidate := range []Kind{ModelPack, HardwareProfile, Release, Evidence} {
				if strings.HasPrefix(filepath.Base(file), string(candidate)) {
					kind = candidate
					break
				}
			}
			data, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			if err := Validate(kind, data); err != nil {
				t.Fatal(err)
			}
			if kind == ModelPack && !strings.Contains(file, "draft") {
				if err := ValidateDeployableModelPack(data); err != nil {
					t.Fatal(err)
				}
			}
			if kind == ModelPack || kind == HardwareProfile {
				var value any
				if err := json.Unmarshal(data, &value); err != nil {
					t.Fatal(err)
				}
				encoded, err := yaml.Marshal(value)
				if err != nil {
					t.Fatal(err)
				}
				if err := Validate(kind, encoded); err != nil {
					t.Fatalf("YAML: %v", err)
				}
			}
		})
	}
}

func TestInvalidFixtures(t *testing.T) {
	for _, kind := range []Kind{ModelPack, DeployableModelPack, HardwareProfile, Release, Evidence} {
		files, err := filepath.Glob(filepath.Join("testdata/invalid", string(kind), "*.json"))
		if err != nil || len(files) == 0 {
			t.Fatal("missing invalid fixtures for", kind, err)
		}
		for _, file := range files {
			t.Run(string(kind)+"/"+filepath.Base(file), func(t *testing.T) {
				data, err := os.ReadFile(file)
				if err != nil {
					t.Fatal(err)
				}
				if !json.Valid(data) {
					t.Fatal("fixture must isolate validation, not malformed JSON")
				}
				if err := Validate(kind, data); err == nil {
					t.Fatal("invalid fixture accepted")
				}
			})
		}
	}
}

func TestParserBoundaries(t *testing.T) {
	valid := fixture(t, "valid/model-pack.json")
	for name, data := range map[string][]byte{
		"empty":                 nil,
		"malformed":             []byte("{"),
		"multiple-yaml":         []byte("{}\n---\n{}"),
		"multiple-json":         []byte("{} {}"),
		"duplicate-json":        []byte(`{"schema":"nib.model-pack/v1","schema":"nib.model-pack/v1"}`),
		"duplicate-escaped-key": []byte(`{"schema":"x","\u0073chema":"y"}`),
		"duplicate-yaml":        []byte("id: one\nid: two"),
		"non-string-key":        []byte("1: value"),
		"complex-key":           []byte("? [a,b]\n: value"),
		"alias":                 []byte("a: &a value\nb: *a"),
		"merge":                 []byte("<<: {a: b}"),
		"tag":                   []byte("a: !custom value"),
		"binary":                []byte("a: !!binary dGVzdA=="),
		"invalid-utf8":          {0xff},
		"oversize":              bytes.Repeat([]byte(" "), MaxDocumentBytes+1),
		"deep":                  []byte(strings.Repeat("[", 66) + "0" + strings.Repeat("]", 66)),
	} {
		t.Run(name, func(t *testing.T) {
			if err := ValidateModelPack(data); err == nil {
				t.Fatal("accepted invalid input")
			}
		})
	}
	if err := Validate("unknown", valid); err == nil {
		t.Fatal("unknown kind accepted")
	}
	if err := ValidateRelease([]byte("schema: nib.release/v1")); err == nil {
		t.Fatal("release accepted YAML")
	}
	if err := ValidateEvidence([]byte("schema: nib.evidence/v1")); err == nil {
		t.Fatal("evidence accepted YAML")
	}
	if err := ValidateModelPack(append([]byte("# YAML comment\n"), valid...)); err != nil {
		t.Fatal(err)
	}
}

func TestExactNumbers(t *testing.T) {
	base := fixture(t, "valid/model-pack.json")
	for _, literal := range []string{"1.0000000000000001", "9007199254740991.1", "10000000000000001e-16", "9.0071992547409911e15"} {
		t.Run(literal, func(t *testing.T) {
			data := bytes.Replace(base, []byte(`"total_bytes": 3`), []byte(`"total_bytes": `+literal), 1)
			for _, prefix := range []string{"", "# force YAML conversion\n"} {
				if err := ValidateModelPack(append([]byte(prefix), data...)); err == nil {
					t.Fatal("fraction rounded to integer")
				}
			}
		})
	}
	for _, literal := range []string{"3.0", "3e0", "0.3e1", "9007199254740991"} {
		data := bytes.Replace(base, []byte(`"total_bytes": 3`), []byte(`"total_bytes": `+literal), 1)
		for _, prefix := range []string{"", "# YAML\n"} {
			if err := ValidateModelPack(append([]byte(prefix), data...)); err != nil {
				t.Fatal(literal, err)
			}
		}
	}
	for _, literal := range []string{".nan", ".inf", "0x10", "0o10", "010", "+1", "1_000"} {
		if _, err := decode([]byte("number: "+literal), false); err == nil {
			t.Fatalf("non-JSON number %s accepted", literal)
		}
	}
	hardware := fixture(t, "valid/hardware-profile-single.json")
	for _, literal := range []string{"1e0", "1.0"} {
		data := bytes.Replace(hardware, []byte(`"count": 1`), []byte(`"count": `+literal), 1)
		if err := ValidateHardwareProfile(data); err != nil {
			t.Fatal(literal, err)
		}
	}
	hardware = bytes.Replace(hardware, []byte(`"count": 1`), []byte(`"count": 1.0000000000000001`), 1)
	if err := ValidateHardwareProfile(hardware); err == nil {
		t.Fatal("fractional GPU count rounded")
	}
}

func TestEvidenceNeverPromotesFakes(t *testing.T) {
	for _, execution := range []string{"fake", "software", "not_run"} {
		name := "valid/evidence.json"
		if execution == "not_run" {
			name = "valid/evidence-not-run.json"
		}
		data := fixture(t, name)
		var v map[string]any
		if err := json.Unmarshal(data, &v); err != nil {
			t.Fatal(err)
		}
		v["execution"], v["hardware_validated"] = execution, true
		data, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateEvidence(data); err == nil {
			t.Fatal("fake hardware promotion", execution)
		}
	}
}

func TestErrorsDoNotEchoValues(t *testing.T) {
	data := bytes.Replace(fixture(t, "valid/evidence.json"), []byte(`"command": "fixture-only; never executed"`), []byte(`"command": {"value": "private-sentinel"}`), 1)
	err := ValidateEvidence(data)
	if err == nil || strings.Contains(err.Error(), "private-sentinel") {
		t.Fatalf("unexpected diagnostic: %v", err)
	}
}

func TestConcurrentValidation(t *testing.T) {
	data := fixture(t, "valid/model-pack.json")
	for i := 0; i < 20; i++ {
		t.Run("parallel", func(t *testing.T) {
			t.Parallel()
			if err := ValidateModelPack(data); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func FuzzValidate(f *testing.F) {
	for _, name := range []string{"model-pack", "hardware-profile", "release", "evidence"} {
		data, err := os.ReadFile("testdata/valid/" + name + ".json")
		if err != nil {
			f.Fatal(err)
		}
		f.Add(data)
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		for _, kind := range []Kind{ModelPack, DeployableModelPack, HardwareProfile, Release, Evidence} {
			_ = Validate(kind, data)
		}
	})
}

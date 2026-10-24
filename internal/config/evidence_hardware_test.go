// SPDX-License-Identifier: Apache-2.0

package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestHardwareEvidenceVersionBoundary(t *testing.T) {
	base := fixture(t, "valid/evidence-hardware-failed-unknown.json")
	for _, result := range []string{"pass", "fail"} {
		for _, validated := range []bool{false, true} {
			for _, versions := range []struct {
				name         string
				driver, cuda any
				known        bool
			}{
				{"known", "1.2.3", "1.2.3", true},
				{"unknown-driver", nil, "1.2.3", false},
				{"unknown-cuda", "1.2.3", nil, false},
				{"unknown-both", nil, nil, false},
			} {
				t.Run(fmt.Sprintf("%s/validated-%t/%s", result, validated, versions.name), func(t *testing.T) {
					var doc map[string]any
					if err := json.Unmarshal(base, &doc); err != nil {
						t.Fatal(err)
					}
					doc["result"], doc["hardware_validated"] = result, validated
					host := doc["host"].(map[string]any)
					host["driver"], host["cuda"] = versions.driver, versions.cuda
					raw, err := json.Marshal(doc)
					if err != nil {
						t.Fatal(err)
					}
					wantValid := (result == "fail" && !validated) || (result == "pass" && versions.known)
					if err := ValidateEvidence(raw); (err == nil) != wantValid {
						t.Fatalf("validity = %v, want %v: %v", err == nil, wantValid, err)
					}
				})
			}
		}
	}
}

func TestFailedHardwareUnknownVersionsRequireSelectedTarget(t *testing.T) {
	base := fixture(t, "valid/evidence-hardware-failed-unknown.json")
	for _, target := range []struct {
		name  string
		gpus  any
		valid bool
	}{
		{"four-b200", "four-selected-B200", true},
		{"four-b300", "four-selected-B300", true},
		{"eight-b200", "eight-selected-B200", true},
		{"eight-b300", "eight-selected-B300", true},
		{"unobserved", "unobserved", false},
		{"none", "none", false},
		{"unknown", "unknown", false},
		{"appraisal-scope", "eight-device appraisal scope", false},
		{"not-used", "not-used", false},
		{"inventory-prose", "reported inventory only", false},
		{"synthetic", "synthetic", false},
		{"one", "one-selected-B300", false},
		{"two", "two-selected-B300", false},
		{"numeric-count", "8-selected-B300", false},
		{"h100", "four-selected-H100", false},
		{"h200", "eight-selected-H200", false},
		{"unsupported-family", "eight-selected-B400", false},
		{"lowercase-family", "eight-selected-b300", false},
		{"prefix", "prefix-eight-selected-B300", false},
		{"suffix", "eight-selected-B300-suffix", false},
		{"trailing-newline", "eight-selected-B300\n", false},
		{"empty", "", false},
		{"missing", nil, false},
		{"null", nil, false},
		{"number", 8, false},
		{"boolean", false, false},
		{"array", []string{"eight-selected-B300"}, false},
		{"object", map[string]any{"target": "eight-selected-B300"}, false},
	} {
		for _, versions := range []struct {
			name         string
			driver, cuda any
		}{
			{"unknown-both", nil, nil},
			{"unknown-driver", nil, "1.2.3"},
			{"unknown-cuda", "1.2.3", nil},
		} {
			t.Run(target.name+"/"+versions.name, func(t *testing.T) {
				var doc map[string]any
				if err := json.Unmarshal(base, &doc); err != nil {
					t.Fatal(err)
				}
				host := doc["host"].(map[string]any)
				host["driver"], host["cuda"] = versions.driver, versions.cuda
				if target.name == "missing" {
					delete(host, "gpus")
				} else {
					host["gpus"] = target.gpus
				}
				raw, err := json.Marshal(doc)
				if err != nil {
					t.Fatal(err)
				}
				if err := ValidateEvidence(raw); (err == nil) != target.valid {
					t.Fatalf("validity = %v, want %v: %v", err == nil, target.valid, err)
				}
			})
		}
	}
}

func TestFailedHardwareEvidenceStillRequiresVersionFields(t *testing.T) {
	base := fixture(t, "valid/evidence-hardware-failed-unknown.json")
	for _, field := range []string{"driver", "cuda"} {
		for _, invalid := range []struct {
			name  string
			value any
		}{
			{"missing", nil},
			{"empty", ""},
			{"unknown-string", "unknown"},
			{"floating", "latest"},
			{"number", 12.3},
			{"boolean", false},
		} {
			t.Run(field+"/"+invalid.name, func(t *testing.T) {
				var doc map[string]any
				if err := json.Unmarshal(base, &doc); err != nil {
					t.Fatal(err)
				}
				host := doc["host"].(map[string]any)
				if invalid.name == "missing" {
					delete(host, field)
				} else {
					host[field] = invalid.value
				}
				raw, err := json.Marshal(doc)
				if err != nil {
					t.Fatal(err)
				}
				if err := ValidateEvidence(raw); err == nil {
					t.Fatal("invalid version field accepted")
				}
			})
		}
	}
}

func TestOriginalSelectedFailureEnvelopeSchemaBoundary(t *testing.T) {
	for _, run := range []struct{ name, directory string }{
		{"1736", ""}, {"1741", "evidence"}, {"1800", "evidence"},
	} {
		t.Run(run.name, func(t *testing.T) {
			path := filepath.Join("..", "..", "evidence", "p2",
				"2026-10-24-selected-tau-readiness-failure-"+run.name, run.directory, "run.json")
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateEvidence(raw); err != nil {
				t.Fatal(err)
			}
			for _, promoted := range []struct {
				result    string
				validated bool
			}{
				{"pass", false}, {"fail", true}, {"pass", true},
			} {
				var doc map[string]any
				if err := json.Unmarshal(raw, &doc); err != nil {
					t.Fatal(err)
				}
				doc["result"], doc["hardware_validated"] = promoted.result, promoted.validated
				changed, err := json.Marshal(doc)
				if err != nil {
					t.Fatal(err)
				}
				if err := ValidateEvidence(changed); err == nil {
					t.Fatalf("original failure promoted to result=%s, validated=%t", promoted.result, promoted.validated)
				}
			}
		})
	}
}

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
	base := fixture(t, "valid/evidence-hardware-failed.json")
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

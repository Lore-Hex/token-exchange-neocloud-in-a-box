// SPDX-License-Identifier: Apache-2.0
package config

import (
	"bytes"
	"strings"
	"testing"
)

func TestNumericResourceLimits(t *testing.T) {
	base := fixture(t, "valid/model-pack.json")
	for _, literal := range []string{"1e1000001", "1e-1000001", "1e1000000", "1e-1000000", "3e1025", "3e-1025", "3e99999999999999999999", strings.Repeat("1", 129), "3." + strings.Repeat("0", 128)} {
		for _, prefix := range []string{"", "# YAML\n"} {
			data := append([]byte(prefix), bytes.Replace(base, []byte(`"total_bytes": 3`), []byte(`"total_bytes": `+literal), 1)...)
			if err := ValidateModelPack(data); err == nil {
				t.Fatal("unbounded numeric representation accepted")
			}
			if _, err := decode(data, false); prefix == "" && err == nil {
				t.Fatalf("unbounded JSON number reached the schema validator: %s", literal)
			}
		}
	}
}

func TestNumericLimitsPreserveExactValuesAndStrings(t *testing.T) {
	for _, input := range []string{
		`{"value":1e1024}`, `{"value":1E-1024}`, `{"value":3e+0}`,
		`{"value":1.0000000000000001}`, `{"value":"1e1000001"}`,
		`{"value":[{"number":9007199254740991}]}`,
	} {
		if _, err := decode([]byte(input), true); err != nil {
			t.Fatal(err)
		}
	}
	for _, input := range []string{
		`{"value":[{"number":1e1025}]}`,
		`[{"nested":[1e-1025]}]`,
	} {
		if _, err := decode([]byte(input), true); err == nil {
			t.Fatal("nested unbounded exponent accepted")
		}
	}
}

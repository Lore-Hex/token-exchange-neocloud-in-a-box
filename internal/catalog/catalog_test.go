// SPDX-License-Identifier: Apache-2.0

package catalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func fixtureRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for _, file := range []string{
		"catalog/pricing.yaml",
		"models/deepseek-v4.1-flash-1017/1/pack.yaml", "models/deepseek-v4.1-flash-1017/1/files.sha256",
		"models/glm-5.3/1/pack.yaml", "models/glm-5.3/1/files.sha256",
	} {
		data, err := os.ReadFile(filepath.Join("../..", file))
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, root, file, data)
	}
	return root
}

func writeFile(t *testing.T, root, file string, data []byte) {
	t.Helper()
	name := filepath.Join(root, file)
	if err := os.MkdirAll(filepath.Dir(name), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(name, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func rewrite(t *testing.T, root, file, old, new string) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, file))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), old) {
		t.Fatalf("test replacement not found: %q", old)
	}
	writeFile(t, root, file, []byte(strings.ReplaceAll(string(data), old, new)))
}

func TestFixtureGoldenAndDeterminism(t *testing.T) {
	root := fixtureRoot(t)
	golden, err := os.ReadFile("../../catalog/testdata/catalog.v2.golden.json")
	if err != nil {
		t.Fatal(err)
	}
	for range 3 {
		got, err := Generate(root, "catalog/pricing.yaml")
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got, golden) {
			t.Fatal("catalog differs from golden output")
		}
		if err := Validate(got); err != nil {
			t.Fatal(err)
		}
	}
	data, _ := os.ReadFile(filepath.Join(root, "catalog/pricing.yaml"))
	value, err := decode(data)
	if err != nil {
		t.Fatal(err)
	}
	doc := value.(map[string]any)
	rows := doc["models"].([]any)
	rows[0], rows[1] = rows[1], rows[0]
	reordered, _ := json.Marshal(doc)
	writeFile(t, root, "reordered.json", reordered)
	got, err := Generate(root, "reordered.json")
	if err != nil || !bytes.Equal(got, golden) {
		t.Fatalf("input ordering changed output: %v", err)
	}
}

func TestLongCompletionsRequireExplicitV21(t *testing.T) {
	golden, err := os.ReadFile("../../catalog/testdata/catalog.v2.golden.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, version := range []string{"2.0", "2.1", "2.2", ""} {
		for _, seconds := range []int{0, 900, 901, 3540, 3600, 3601} {
			var doc map[string]any
			if err := json.Unmarshal(golden, &doc); err != nil {
				t.Fatal(err)
			}
			doc["contract_version"] = version
			for _, row := range doc["data"].([]any) {
				row.(map[string]any)["reliability"].(map[string]any)["completion_timeout_seconds"] = seconds
			}
			raw, err := json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			valid := seconds > 0 && (version == "2.0" && seconds <= 900 || version == "2.1" && seconds <= 3600)
			if (Validate(raw) == nil) != valid {
				t.Fatalf("unexpected catalog acceptance for version %q, %d seconds", version, seconds)
			}
		}
	}
}

func TestExactDecimalPrices(t *testing.T) {
	for _, price := range []string{"0", "0.000000000000000000000000000001", "9007199254740993.123456789012345678901234567890", "1.2300", strings.Repeat("9", 128)} {
		t.Run(price, func(t *testing.T) {
			root := fixtureRoot(t)
			rewrite(t, root, "catalog/pricing.yaml", `"0.000001"`, `"`+price+`"`)
			rewrite(t, root, "catalog/pricing.yaml", "cached_input: null", `cached_input: "0.000000000000000000001"`)
			rewrite(t, root, "catalog/pricing.yaml", "cache_write: null", `cache_write: "0.000000000000000000002"`)
			got, err := Generate(root, "catalog/pricing.yaml")
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Contains(got, []byte(`"input": "`+price+`"`)) {
				t.Fatal("exact decimal representation lost")
			}
		})
	}
}

func TestRejectInvalidPrices(t *testing.T) {
	for _, field := range []string{"input", "output", "cached_input", "cache_write", "minimum_request"} {
		for _, price := range []string{`"-1"`, `"+1"`, `"01"`, `".1"`, `"1."`, `"1e2"`, `"NaN"`, `"Infinity"`, `" 1"`, `"1\n"`, `""`, "0.1", "1", "true", ".nan", "1e99999999", "0x10", `"` + strings.Repeat("1", 129) + `"`} {
			t.Run(field+"/"+price, func(t *testing.T) {
				root := fixtureRoot(t)
				old := map[string]string{"input": `"0.000001"`, "output": `"0.000002"`, "cached_input": "null", "cache_write": "null", "minimum_request": `"0"`}[field]
				rewrite(t, root, "catalog/pricing.yaml", "      "+field+": "+old, "      "+field+": "+price)
				if got, err := Generate(root, "catalog/pricing.yaml"); err == nil || got != nil {
					t.Fatal("invalid price accepted")
				}
			})
		}
	}
}

func TestRejectInvalidDeclarations(t *testing.T) {
	cases := map[string][2]string{
		"commercial":      {"synthetic-local-test", "commercial"},
		"missing-purpose": {"purpose: synthetic-local-test", ""},
		"unknown-root":    {"models:", "privacy: tee-verified\nmodels:"},
		"duplicate-key":   {"purpose: synthetic-local-test", "purpose: synthetic-local-test\npurpose: synthetic-local-test"},
		"empty-models":    {"models:", "models: []\nignored:"},
		"profile-missing": {"b200x8-gcp-plain", "b200x8-tdx"},
		"pack-missing":    {"models/glm-5.3/1/pack.yaml", "models/missing/1/pack.yaml"},
		"pack-traversal":  {"models/glm-5.3/1/pack.yaml", "models/../outside/pack.yaml"},
		"output-context":  {"max_output_tokens: 16", "max_output_tokens: 1048577"},
		"output-zero":     {"max_output_tokens: 16", "max_output_tokens: 0"},
		"output-overflow": {"max_output_tokens: 16", "max_output_tokens: 9223372036854775808"},
		"output-float":    {"max_output_tokens: 16", "max_output_tokens: 1.0"},
		"currency":        {"currency: USD", "currency: EUR"},
		"unit":            {"unit: per_1m_tokens", "unit: per_token"},
		"required-price":  {`input: "0.000001"`, "input: null"},
		"missing-price":   {`      input: "0.000001"`, ""},
		"privacy":         {"    profile:", "    privacy: tee-verified\n    profile:"},
		"attestation":     {"    profile:", "    attestation: true\n    profile:"},
		"confidential":    {"    profile:", "    confidential: true\n    profile:"},
		"zdr":             {"    profile:", "    zero_data_retention: true\n    profile:"},
		"receipts":        {"    profile:", "    receipts: true\n    profile:"},
		"capabilities":    {"    profile:", "    capabilities: {streaming: true}\n    profile:"},
		"price-claim":     {"      currency:", "      tee_verified: true\n      currency:"},
		"duplicate-model": {"models/glm-5.3/1/pack.yaml", "models/deepseek-v4.1-flash-1017/1/pack.yaml"},
		"anchor":          {"models:", "models: &models"},
		"alias":           {`input: "0.000001"`, "input: *missing"},
		"custom-tag":      {"purpose: synthetic-local-test", "purpose: !claim synthetic-local-test"},
		"multiple-docs":   {"schema: nib.catalog-pricing/v1", "schema: nib.catalog-pricing/v1\n---"},
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			root := fixtureRoot(t)
			rewrite(t, root, "catalog/pricing.yaml", change[0], change[1])
			if got, err := Generate(root, "catalog/pricing.yaml"); err == nil || got != nil {
				t.Fatal("invalid declaration accepted")
			}
		})
	}
}

func TestPackAndPathFailures(t *testing.T) {
	for name, change := range map[string][2]string{
		"bad-pack":            {"hf_revision:", "unknown_field:"},
		"duplicate-canonical": {"tr_model_id: z-ai/glm-5.3", "tr_model_id: deepseek/deepseek-v4.1-flash-1017"},
		"duplicate-native":    {"served_model_name: glm-5.3", "served_model_name: deepseek-v4.1-flash-1017"},
		"bad-total":           {"total_bytes: 755663689302", "total_bytes: 1"},
	} {
		t.Run(name, func(t *testing.T) {
			root := fixtureRoot(t)
			rewrite(t, root, "models/glm-5.3/1/pack.yaml", change[0], change[1])
			if _, err := Generate(root, "catalog/pricing.yaml"); err == nil {
				t.Fatal("invalid pack accepted")
			}
		})
	}
	root := fixtureRoot(t)
	for _, name := range []string{"../pricing.yaml", "/etc/passwd", "catalog", "missing.yaml"} {
		if _, err := Generate(root, name); err == nil {
			t.Fatal("invalid path accepted")
		}
	}
	if err := os.Symlink(filepath.Join(root, "catalog/pricing.yaml"), filepath.Join(root, "link.yaml")); err != nil {
		t.Fatal(err)
	}
	if _, err := Generate(root, "link.yaml"); err == nil {
		t.Fatal("symlink accepted")
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	if _, err := Generate(root, "escape/secret.yaml"); err == nil {
		t.Fatal("escaping directory accepted")
	}
	writeFile(t, root, "catalog/pricing.yaml", bytes.Repeat([]byte(" "), config.MaxDocumentBytes+1))
	if _, err := Generate(root, "catalog/pricing.yaml"); err == nil {
		t.Fatal("oversized file accepted")
	}
}

func TestPinnedSchemaAndDecodeFailures(t *testing.T) {
	for _, data := range [][]byte{nil, []byte("["), {0xff}, []byte("{1: value}"), []byte("x: !!float 1.0"), []byte("x: !!set {}"), []byte("x: [" + strings.Repeat("[", 33) + "1" + strings.Repeat("]", 34))} {
		if _, err := decode(data); err == nil {
			t.Fatal("invalid document accepted")
		}
	}
	got, err := Generate(fixtureRoot(t), "catalog/pricing.yaml")
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range [][2]string{{`"contract_version": "2.0"`, `"contract_version": "3.0"`}, {`"capabilities": {`, `"privacy": "tee-verified", "capabilities": {`}, {`"input": "0.000001"`, `"input": 0.000001`}, {`"status_url": "https://nib.invalid/synthetic-only"`, `"status_url": "invalid-uri"`}} {
		if err := Validate([]byte(strings.ReplaceAll(string(got), change[0], change[1]))); err == nil {
			t.Fatal("invalid catalog accepted by pinned schema")
		}
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("sensitive-write-error") }

func TestCLI(t *testing.T) {
	root := fixtureRoot(t)
	for _, test := range []struct {
		args []string
		code int
	}{{[]string{"--root", root}, 0}, {[]string{"--help"}, 0}, {[]string{"--secret=do-not-echo"}, 2}, {[]string{"extra"}, 2}, {[]string{"--root", root, "--pricing", "do-not-echo"}, 1}} {
		var out, diag bytes.Buffer
		if code := Run(test.args, &out, &diag); code != test.code {
			t.Fatalf("exit %d, want %d: %s", code, test.code, diag.String())
		}
		if test.code != 0 && out.Len() != 0 {
			t.Fatal("output emitted on failure")
		}
		if strings.Contains(diag.String(), "do-not-echo") {
			t.Fatal("diagnostic leaked input")
		}
	}
	var diag bytes.Buffer
	if code := Run([]string{"--root", root}, failingWriter{}, &diag); code != 1 || strings.Contains(diag.String(), "sensitive-write-error") {
		t.Fatal("write failure mishandled")
	}
	rewrite(t, root, "catalog/pricing.yaml", `"0.000001"`, `"sensitive-price"`)
	var out bytes.Buffer
	diag.Reset()
	if code := Run([]string{"--root", root}, &out, &diag); code != 1 || out.Len() != 0 || strings.Contains(diag.String(), "sensitive-price") {
		t.Fatal("validation failure leaked configuration")
	}
}

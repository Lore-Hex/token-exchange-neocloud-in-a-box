// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

func testProvider() ProviderConfig {
	return ProviderConfig{ID: "pilot", StatusURL: "https://example.invalid/healthz",
		SupportContact: "mailto:support@example.invalid", IncidentContact: "mailto:ops@example.invalid",
		Regions: []string{"local-test-only"}}
}

func writeProvider(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "provider.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDistinctOperatorCatalogs(t *testing.T) {
	providers := []ProviderConfig{
		{ID: "operator-a", StatusURL: "https://status.a.example.invalid/healthz",
			SupportContact: "mailto:support@a.example.invalid", IncidentContact: "mailto:ops@a.example.invalid", Regions: []string{"US"}},
		{ID: "operator.b_2", StatusURL: "https://status.b.example.invalid:8443/status",
			SupportContact: "mailto:help@b.example.invalid", IncidentContact: "mailto:incidents@b.example.invalid", Regions: []string{"GB", "eu-west-2"}},
	}
	for _, provider := range providers {
		t.Run(provider.ID, func(t *testing.T) {
			encoded, _ := json.Marshal(provider)
			loaded, err := LoadProviderConfig(writeProvider(t, encoded))
			if err != nil || !reflect.DeepEqual(loaded, provider) {
				t.Fatalf("operator metadata changed: %v", err)
			}
			handler, err := New(loaded, testKey, "0.10", "0.20", "")
			if err != nil {
				t.Fatal(err)
			}
			// Catalog bytes are a startup snapshot even if a caller mutates its slice.
			loaded.Regions[0] = "changed-after-startup"
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest("GET", "/v1/models", nil))
			data := response.Body.Bytes()
			if response.Code != 200 || catalog.Validate(data) != nil {
				t.Fatal("runtime catalog does not satisfy the pinned v2 schema")
			}
			var document struct{ Provider ProviderConfig }
			if err := json.Unmarshal(data, &document); err != nil || !reflect.DeepEqual(document.Provider, provider) {
				t.Fatalf("runtime declaration does not match the operator: %s", data)
			}
			for _, claim := range []string{"privacy", "tee-verified", "attestation"} {
				if bytes.Contains(data, []byte(claim)) {
					t.Fatalf("invented claim: %s", claim)
				}
			}
		})
	}
}

func TestProviderValidationSharedByCatalogAndNew(t *testing.T) {
	mutations := map[string]func(*ProviderConfig){
		"missing-id":          func(p *ProviderConfig) { p.ID = "" },
		"uppercase-id":        func(p *ProviderConfig) { p.ID = "Operator" },
		"leading-id-dash":     func(p *ProviderConfig) { p.ID = "-operator" },
		"id-path":             func(p *ProviderConfig) { p.ID = "a/b" },
		"id-space":            func(p *ProviderConfig) { p.ID = "a b" },
		"id-newline":          func(p *ProviderConfig) { p.ID = "pilot\n" },
		"empty-regions":       func(p *ProviderConfig) { p.Regions = []string{} },
		"nil-regions":         func(p *ProviderConfig) { p.Regions = nil },
		"empty-region":        func(p *ProviderConfig) { p.Regions = []string{""} },
		"duplicate-regions":   func(p *ProviderConfig) { p.Regions = []string{"US", "US"} },
		"region-space":        func(p *ProviderConfig) { p.Regions = []string{" US"} },
		"region-control":      func(p *ProviderConfig) { p.Regions = []string{"US\nCA"} },
		"region-invalid-utf8": func(p *ProviderConfig) { p.Regions = []string{string([]byte{0xff})} },
		"oversize":            func(p *ProviderConfig) { p.ID = strings.Repeat("a", MaxProviderConfigBytes) },
	}
	for _, value := range []string{"", "http://example.invalid", "//example.invalid", "https:example.invalid",
		"https:///path", "https://user:password@example.invalid", "https://example.invalid/#fragment",
		"https://bad_host.invalid", "https://bad..invalid", "https://-bad.invalid", "https://example.invalid:\n",
		"https://[example.invalid]", "https://example.invalid/<status>", "https://example.invalid/?bad=%ZZ",
		"https://example.invalid:", "https://example.invalid:0", "https://example.invalid:65536"} {
		mutations["status:"+value] = func(p *ProviderConfig) { p.StatusURL = value }
	}
	for _, value := range []string{"", "support@example.invalid", "https://example.invalid/support", "mailto:",
		"mailto:not-an-address", "mailto:a@bad_host.invalid", "mailto://a@example.invalid",
		"mailto:a@example.invalid?subject=secret", "mailto:a@example.invalid?", "mailto:a@example.invalid#fragment",
		"mailto:a@example.invalid,b@example.invalid", "mailto:Name%20%3Ca@example.invalid%3E",
		"mailto:a@example.invalid%0D%0ABcc:b@example.invalid"} {
		mutations["support:"+value] = func(p *ProviderConfig) { p.SupportContact = value }
		mutations["incident:"+value] = func(p *ProviderConfig) { p.IncidentContact = value }
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			provider := testProvider()
			mutate(&provider)
			if provider.Validate() == nil {
				t.Fatal("invalid provider accepted")
			}
			if _, err := Catalog(provider, "0.10", "0.20", time.Time{}); err == nil {
				t.Fatal("Catalog bypasses provider validation")
			}
			if _, err := New(provider, testKey, "0.10", "0.20", ""); err == nil {
				t.Fatal("New bypasses provider validation")
			}
		})
	}
	if _, err := New(ProviderConfig{}, testKey, "0.10", "0.20", ""); err == nil {
		t.Fatal("missing provider silently defaulted")
	}
}

func TestProviderFileStrictJSON(t *testing.T) {
	encoded, _ := json.Marshal(testProvider())
	valid := string(encoded)
	for _, value := range []string{"", "null", "[]", "{}", valid + "{}", valid + " trailing",
		strings.Replace(valid, `"id":`, `"ID":`, 1),
		strings.Replace(valid, `"id":`, `"id":"other","id":`, 1),
		strings.Replace(valid, `"id":`, `"\u0069d":"other","id":`, 1),
		strings.Replace(valid, `"id":"pilot",`, "", 1),
		strings.Replace(valid, `"pilot"`, "null", 1),
		strings.Replace(valid, `"pilot"`, "123", 1),
		strings.Replace(valid, `["local-test-only"]`, "null", 1),
		strings.Replace(valid, `["local-test-only"]`, `[null]`, 1),
		strings.Replace(valid, `["local-test-only"]`, `[123]`, 1),
		strings.Replace(valid, `["local-test-only"]`, `"US"`, 1),
		strings.Replace(valid, "}", `,"privacy":"tee-verified"}`, 1),
		strings.Replace(valid, "}", `,"request_id_header":"Alternate"}`, 1),
		strings.Replace(valid, "}", `,"country":"US"}`, 1),
		strings.Replace(valid, "}", `,"unknown":{}}`, 1),
		strings.Replace(valid, "local-test-only", string([]byte{0xff}), 1),
	} {
		if _, err := LoadProviderConfig(writeProvider(t, []byte(value))); err == nil {
			t.Fatalf("accepted invalid JSON: %q", value)
		}
	}
	for _, size := range []int{len(valid), MaxProviderConfigBytes, MaxProviderConfigBytes + 1} {
		path := writeProvider(t, []byte(valid+strings.Repeat(" ", size-len(valid))))
		_, err := LoadProviderConfig(path)
		if (err == nil) != (size <= MaxProviderConfigBytes) {
			t.Fatalf("incorrect file size boundary at %d: %v", size, err)
		}
	}
}

func TestProviderFileMustBeRegularAndNotSymlink(t *testing.T) {
	root := t.TempDir()
	encoded, _ := json.Marshal(testProvider())
	path := writeProvider(t, encoded)
	link, fifo := filepath.Join(root, "link"), filepath.Join(root, "fifo")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"", filepath.Join(root, "missing"), root, link, fifo, "/dev/null"} {
		if _, err := LoadProviderConfig(path); err == nil {
			t.Fatalf("accepted non-regular file: %s", path)
		}
	}
}

func TestCheckedInExampleIsLocalOnly(t *testing.T) {
	provider, err := LoadProviderConfig("../../examples/provider.json")
	if err != nil || provider.ID != "acme" || !reflect.DeepEqual(provider.Regions, []string{"US"}) {
		t.Fatalf("invalid onboarding example: %v", err)
	}
	if provider.ValidatePublic() == nil {
		t.Fatal("public mode accepted the example's placeholder contacts")
	}
	data, err := Catalog(provider, "0.10", "0.20", time.Time{})
	if err != nil || catalog.Validate(data) != nil {
		t.Fatalf("onboarding example cannot produce a local v2 catalog: %v", err)
	}
}

func TestPublicProviderRejectsReservedDomains(t *testing.T) {
	provider := ProviderConfig{ID: "operator", StatusURL: "https://status.operator-a.org/healthz",
		SupportContact: "mailto:support@operator-a.org", IncidentContact: "mailto:ops@operator-a.org", Regions: []string{"US"}}
	if err := provider.ValidatePublic(); err != nil {
		t.Fatal(err)
	}
	for _, host := range []string{"example.invalid", "api.example.invalid", "EXAMPLE.INVALID.", "example.com", "api.example.org",
		"example.net", "service.example", "local.test", "localhost", "api.localhost"} {
		for _, field := range []string{"status", "support", "incident"} {
			changed := provider
			switch field {
			case "status":
				changed.StatusURL = "https://" + host + "/healthz"
			case "support":
				changed.SupportContact = "mailto:support@" + host
			case "incident":
				changed.IncidentContact = "mailto:ops@" + host
			}
			if changed.ValidatePublic() == nil {
				t.Fatalf("public %s accepted reserved host %s", field, host)
			}
		}
	}
}

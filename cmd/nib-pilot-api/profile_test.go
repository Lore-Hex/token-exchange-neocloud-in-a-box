// SPDX-License-Identifier: Apache-2.0
package main

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

func TestCLIClosedProfileSelection(t *testing.T) {
	keyFile := filepath.Join(t.TempDir(), "api-key")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("synthetic-test-", 5)+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	base := []string{"--api-key-file", keyFile, "--provider-config", "../../examples/provider.json",
		"--input-price", "0.10", "--output-price", "0.20"}
	var defaultCatalog string
	for _, tc := range []struct {
		args  []string
		model string
	}{
		{nil, "qwen/qwen3-0.6b"},
		{[]string{"--profile", "qwen"}, "qwen/qwen3-0.6b"},
		{[]string{"--profile", "selected-deepseek"}, "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"},
	} {
		handler, err := configuredHandler(append(append([]string{}, base...), tc.args...))
		if err != nil {
			t.Fatal(err)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("GET", "/v1/models", nil))
		if w.Code != 200 || catalog.Validate(w.Body.Bytes()) != nil || !strings.Contains(w.Body.String(), `"id":"`+tc.model+`"`) {
			t.Fatal("CLI selected the wrong catalog")
		}
		if tc.args == nil {
			defaultCatalog = w.Body.String()
		} else if tc.model == "qwen/qwen3-0.6b" && w.Body.String() != defaultCatalog {
			t.Fatal("explicit Qwen changed default wire bytes")
		}
	}
	for _, name := range []string{"", "unknown", "Qwen", "selected-deepseek ", "deepseek-v4.1-flash-1017-uncensored-fp8"} {
		if _, err := configuredHandler(append(append([]string{}, base...), "--profile", name)); err == nil {
			t.Fatal("CLI accepted an unknown/empty profile")
		}
	}
	selected := append(append([]string{}, base...), "--profile", "selected-deepseek", "--public")
	if _, err := configuredHandler(selected); err == nil {
		t.Fatal("selected public mode bypasses operator/cutoff requirements")
	}
	if _, err := configuredHandler(append(selected, "--retirement-at", time.Now().Add(time.Hour).UTC().Format(time.RFC3339))); err == nil {
		t.Fatal("selected public mode accepted placeholder operator metadata")
	}
}

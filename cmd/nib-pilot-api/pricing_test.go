// SPDX-License-Identifier: Apache-2.0
package main

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/pilotapi"
)

func TestCachePriceCLIIsExplicitAndProfileBound(t *testing.T) {
	keyFile := filepath.Join(t.TempDir(), "api-key")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("x", 64)), 0600); err != nil {
		t.Fatal(err)
	}
	base := []string{"--api-key-file", keyFile, "--provider-config", "../../examples/provider.json",
		"--input-price", "0.65", "--output-price", "1.45"}
	for _, test := range []struct {
		profile string
		flags   []string
		want    string
	}{
		{pilotapi.ProfileSelectedMillion, nil, `"cached_input":"0.65"`},
		{pilotapi.ProfileSelectedMillion, []string{"--cached-input-price", "0.065"}, `"cached_input":"0.065"`},
		{pilotapi.ProfileSelectedMillion, []string{"--cached-input-price", "0"}, `"cached_input":"0"`},
		{pilotapi.ProfileQwen, nil, `"cached_input":null`},
		{pilotapi.ProfileQwen, []string{"--cached-input-price", "0"}, ""},
		{pilotapi.ProfileSelectedMillion, []string{"--cached-input-price", ""}, ""},
		{pilotapi.ProfileSelectedMillion, []string{"--cached-input-price", "0.650001"}, ""},
		{pilotapi.ProfileSelectedMillion, []string{"--cached-input-price", "0", "--cached-input-price", "0"}, ""},
	} {
		args := append(append([]string{}, base...), "--profile", test.profile)
		h, err := configuredHandler(append(args, test.flags...))
		if (err == nil) != (test.want != "") {
			t.Fatalf("cache flag admission mismatch: %v", err)
		}
		if err == nil {
			response := httptest.NewRecorder()
			h.ServeHTTP(response, httptest.NewRequest("GET", "/v1/models", nil))
			if response.Code != 200 || !strings.Contains(response.Body.String(), test.want) {
				t.Fatal("cache flag did not reach public catalog")
			}
		}
	}
}

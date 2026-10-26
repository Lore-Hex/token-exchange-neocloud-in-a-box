// SPDX-License-Identifier: Apache-2.0
// Emits generated catalogs for checker interoperability tests; no listener or inference.
package main

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"strings"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/pilotapi"
)

func main() {
	provider := pilotapi.ProviderConfig{ID: "pilot", StatusURL: "https://example.invalid/healthz",
		SupportContact: "mailto:support@example.invalid", IncidentContact: "mailto:ops@example.invalid",
		Regions: []string{"local-test-only"}}
	result := map[string]json.RawMessage{}
	for _, name := range []string{pilotapi.ProfileQwen, pilotapi.ProfileSelectedDeepSeek,
		pilotapi.ProfileSelectedGraphs, pilotapi.ProfileSelectedQuality,
		pilotapi.ProfileSelectedLong, pilotapi.ProfileSelectedMillion} {
		h, err := pilotapi.NewWithProfile(name, provider, strings.Repeat("a", 64), "0.65", "1.45", "")
		if err != nil {
			os.Exit(1)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", "/v1/models", nil))
		if w.Code != 200 || !json.Valid(w.Body.Bytes()) {
			os.Exit(1)
		}
		result[name] = w.Body.Bytes()
	}
	if json.NewEncoder(os.Stdout).Encode(result) != nil {
		os.Exit(1)
	}
}

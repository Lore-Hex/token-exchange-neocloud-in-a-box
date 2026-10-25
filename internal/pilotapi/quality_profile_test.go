// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSelectedQualityProfileBounds(t *testing.T) {
	h, err := NewWithProfile(ProfileSelectedQuality, testProvider(), testKey, "0.10", "0.20", "")
	if err != nil {
		t.Fatal(err)
	}
	if h.profile.context != 32768 || h.profile.maxOutput != 2048 || cap(h.slots) != 1 || h.profile.model != selectedModel {
		t.Fatal("quality profile limit or identity mismatch")
	}
	if !strings.Contains(string(h.catalog), `"context_length":32768`) || !strings.Contains(string(h.catalog), `"max_output_tokens":2048`) {
		t.Fatal("catalog does not describe enforced quality limits")
	}
	for _, fields := range []string{"", `,"max_tokens":2048`, `,"max_completion_tokens":2048`} {
		body, err := h.profile.boundRequest([]byte(selectedRequest(fields)))
		if err != nil {
			t.Fatal(err)
		}
		var got map[string]any
		if err := json.Unmarshal(body, &got); err != nil || got["max_tokens"] != float64(2048) || got["model"] != selectedModel {
			t.Fatal("quality request changed identity or output ceiling")
		}
	}
	for _, fields := range []string{`,"max_tokens":2049`, `,"max_tokens":0`, `,"max_tokens":null`, `,"max_tokens":true`, `,"max_tokens":1,"max_completion_tokens":2049`} {
		if _, err := h.profile.boundRequest([]byte(selectedRequest(fields))); err == nil {
			t.Fatal("invalid quality limit accepted")
		}
	}
	if _, err := selectedHandler(t).profile.boundRequest([]byte(selectedRequest(`,"max_tokens":2048`))); err == nil {
		t.Fatal("quality selection silently changed default capacity")
	}
	for _, claim := range []string{"privacy", "confidential", "zdr", "e2ee", "attestation"} {
		if strings.Contains(string(h.catalog), claim) {
			t.Fatalf("quality profile invented %s", claim)
		}
	}
}

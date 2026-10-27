// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestWeeklyLeaseRequiresExplicitOptIn(t *testing.T) {
	cutoff := time.Now().Add(6 * 24 * time.Hour).UTC().Format(time.RFC3339)
	if _, err := NewWithProfile(ProfileSelectedMillion, testProvider(), testKey, "0.65", "1.45", cutoff); err == nil {
		t.Fatal("default lease exceeded one day")
	}
	h, err := NewWithLifetime(ProfileSelectedMillion, testProvider(), testKey, "0.65", "1.45", cutoff, "ordinary-7d")
	if err != nil || !strings.Contains(string(h.catalog), cutoff) {
		t.Fatalf("weekly lease/catalog mismatch: %v", err)
	}
	for _, row := range []struct{ profile, cutoff, lifetime string }{
		{ProfileSelectedMillion, time.Now().Add(8 * 24 * time.Hour).UTC().Format(time.RFC3339), "ordinary-7d"},
		{ProfileSelectedMillion, "", "ordinary-7d"},
		{ProfileSelectedMillion, cutoff, "unlimited"},
		{ProfileQwen, cutoff, "ordinary-7d"},
	} {
		if _, err := NewWithLifetime(row.profile, testProvider(), testKey, "0.65", "1.45", row.cutoff, row.lifetime); err == nil {
			t.Fatal("invalid weekly lease accepted")
		}
	}
	h.now = func() time.Time { return h.retirement }
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/v1/models", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatal("expired weekly lease still advertised its catalog")
	}
}

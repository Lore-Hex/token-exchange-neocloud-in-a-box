// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLongCacheAffinityIsStableAndIgnoresCallerRank(t *testing.T) {
	for _, profile := range []string{ProfileSelectedLong, ProfileSelectedMillion} {
		t.Run(profile, func(t *testing.T) { checkLongCacheAffinity(t, profile) })
	}
}

func checkLongCacheAffinity(t *testing.T, profile string) {
	t.Helper()
	h := longProfileHandler(t, profile)
	var salts, ranks []string
	h.proxy.Transport = longRoundTripper(func(r *http.Request) (*http.Response, error) {
		var body map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		var salt string
		if err := json.Unmarshal(body["cache_salt"], &salt); err != nil {
			t.Fatal(err)
		}
		rank := r.Header.Get("X-data-parallel-rank")
		if rank != selectedCacheRank(body["cache_salt"]) || len(r.Header.Values("X-data-parallel-rank")) != 1 {
			t.Fatal("replica rank was not derived solely from normalized cache namespace")
		}
		salts, ranks = append(salts, salt), append(ranks, rank)
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("{}"))}, nil
	})
	request := func(extra string) {
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(selectedRequest(extra)))
		r.Header.Set("Authorization", "Bearer "+testKey)
		r.Header.Add("X-data-parallel-rank", "999")
		r.Header.Add("X-data-parallel-rank", "-1")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != http.StatusOK {
			t.Fatalf("HTTP %d", w.Code)
		}
	}
	scope := `,"cache_salt":"` + strings.Repeat("a", 64) + `"`
	request(scope)
	request(scope + `,"temperature":0`)
	if salts[0] != salts[1] || ranks[0] != ranks[1] {
		t.Fatal("same namespace did not retain replica and cache identity")
	}
	request("")
	request("")
	if salts[2] == salts[3] {
		t.Fatal("omitted namespace reused another request's cache scope")
	}

	seen := map[string]bool{}
	for i := 0; i < 32; i++ {
		request(fmt.Sprintf(`,"cache_salt":"%064x"`, i))
		seen[ranks[len(ranks)-1]] = true
	}
	if len(seen) != 2 || !seen["0"] || !seen["1"] {
		t.Fatal("namespace distribution does not cover exactly two replicas")
	}
}

func TestLegacyProfilesDoNotForwardCacheAffinity(t *testing.T) {
	for _, profile := range []string{ProfileQwen, ProfileSelectedDeepSeek, ProfileSelectedGraphs, ProfileSelectedQuality} {
		t.Run(profile, func(t *testing.T) {
			h, err := NewWithProfile(profile, testProvider(), testKey, "1", "2", "")
			if err != nil {
				t.Fatal(err)
			}
			h.proxy.Transport = longRoundTripper(func(r *http.Request) (*http.Response, error) {
				if len(r.Header.Values("X-data-parallel-rank")) != 0 {
					t.Fatal("legacy profile forwarded an untrusted replica header")
				}
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("{}"))}, nil
			})
			body := `{"model":"` + h.profile.model + `","messages":[{"role":"user","content":"test"}]}`
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
			r.Header.Set("Authorization", "Bearer "+testKey)
			r.Header.Set("X-data-parallel-rank", "1")
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if w.Code != http.StatusOK {
				t.Fatalf("HTTP %d", w.Code)
			}
		})
	}
}

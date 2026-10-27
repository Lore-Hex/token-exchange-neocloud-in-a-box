// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"bytes"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

func TestExplicitCachePriceOnlyChangesCatalogTariff(t *testing.T) {
	baseline, err := NewWithLifetime(ProfileSelectedMillion, testProvider(), testKey, "0.65", "1.45", "", "ordinary-24h")
	if err != nil {
		t.Fatal(err)
	}
	for _, price := range []string{"0", "0.000001", "0.065", "0.650000"} {
		h, err := NewWithPricing(ProfileSelectedMillion, testProvider(), testKey,
			Pricing{Input: "0.65", Output: "1.45", CachedInput: &price}, "", "ordinary-24h")
		if err != nil || catalog.Validate(h.catalog) != nil {
			t.Fatal("valid cache tariff rejected", err)
		}
		want := bytes.Replace(baseline.catalog, []byte(`"cached_input":"0.65"`), []byte(`"cached_input":"`+price+`"`), 1)
		if !bytes.Equal(h.catalog, want) || h.profile != baseline.profile || cap(h.slots) != cap(baseline.slots) || h.key != baseline.key {
			t.Fatal("cache price changed something other than the declared tariff")
		}
		before := bytes.Clone(h.catalog)
		price = "0.64"
		if !bytes.Equal(before, h.catalog) {
			t.Fatal("caller mutation changed frozen catalog")
		}
	}
}

func TestCachePricingDefaultsPreserveEveryProfile(t *testing.T) {
	for _, profile := range []string{ProfileQwen, ProfileSelectedDeepSeek, ProfileSelectedGraphs,
		ProfileSelectedQuality, ProfileSelectedLong, ProfileSelectedMillion} {
		before, err := NewWithLifetime(profile, testProvider(), testKey, "0.65", "1.45", "", "ordinary-24h")
		if err != nil {
			t.Fatal(err)
		}
		after, err := NewWithPricing(profile, testProvider(), testKey, Pricing{Input: "0.65", Output: "1.45"}, "", "ordinary-24h")
		if err != nil || !bytes.Equal(before.catalog, after.catalog) {
			t.Fatal("omitted cache price changed catalog bytes", err)
		}
		if profile != ProfileSelectedMillion {
			zero := "0"
			if _, err := NewWithPricing(profile, testProvider(), testKey,
				Pricing{Input: "0.65", Output: "1.45", CachedInput: &zero}, "", "ordinary-24h"); err == nil {
				t.Fatal("unqualified profile advertised a cache tariff")
			}
		}
	}
}

func TestCachePriceValidationIsExactAndBounded(t *testing.T) {
	for _, value := range []string{"", " ", "-1", "+0.1", "1e-2", "NaN", "Infinity", "00", "01.0",
		".1", "0.", "0.0000001", "1000000", "0.650001", "1", "0.1\n", strings.Repeat("9", 10000)} {
		if _, err := NewWithPricing(ProfileSelectedMillion, testProvider(), testKey,
			Pricing{Input: "0.65", Output: "1.45", CachedInput: &value}, "", "ordinary-24h"); err == nil {
			t.Fatal("invalid or more expensive cache price accepted")
		}
	}
	zero := "0"
	if _, err := NewWithPricing(ProfileSelectedMillion, testProvider(), testKey,
		Pricing{Input: "0", Output: "0", CachedInput: &zero}, "", "ordinary-24h"); err != nil {
		t.Fatal("explicit zero rejected", err)
	}
	for _, pricing := range []Pricing{{Input: "", Output: "1", CachedInput: &zero}, {Input: "1", Output: "NaN", CachedInput: &zero}} {
		if _, err := NewWithPricing(ProfileSelectedMillion, testProvider(), testKey, pricing, "", "ordinary-24h"); err == nil {
			t.Fatal("cache override bypassed ordinary price validation")
		}
	}
}

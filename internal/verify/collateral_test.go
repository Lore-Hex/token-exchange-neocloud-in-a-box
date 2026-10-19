//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

const testCollateralURL = "https://api.trustedservices.intel.com/tdx/certification/v4/qe/identity"

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestCollateralURLAllowlist(t *testing.T) {
	for _, raw := range []string{testCollateralURL, "https://api.trustedservices.intel.com/tdx/certification/v4/tcb?fmspc=50806f000000", "https://api.trustedservices.intel.com/sgx/certification/v4/pckcrl?ca=platform&encoding=der", "https://certificates.trustedservices.intel.com/IntelSGXRootCA.der"} {
		if !allowedCollateral(raw) {
			t.Errorf("rejected pinned Intel endpoint %q", raw)
		}
	}
	for _, raw := range []string{
		"http://api.trustedservices.intel.com/tdx/certification/v4/qe/identity",
		"https://secret@api.trustedservices.intel.com/tdx/certification/v4/qe/identity",
		"https://api.trustedservices.intel.com.attacker.invalid/tdx/certification/v4/qe/identity",
		"https://api.trustedservices.intel.com:444/tdx/certification/v4/qe/identity",
		"https://api.trustedservices.intel.com/tdx/certification/v4/tcb?fmspc=50806f000000&fmspc=50806f000000",
		"https://api.trustedservices.intel.com/tdx/certification/v4/tcb?fmspc=wrong",
		"https://api.trustedservices.intel.com/tdx/certification/v4/tcb?fmspc=50806f000000&secret=1",
		"https://certificates.trustedservices.intel.com/other.der",
		testCollateralURL + "#secret", testCollateralURL + "?secret=1",
	} {
		if allowedCollateral(raw) {
			t.Errorf("unsafe collateral URL accepted %q", raw)
		}
	}
}

func TestCollateralBudgetAndNoRedirect(t *testing.T) {
	g := newCollateralGetter()
	calls := 0
	g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 200, Header: http.Header{"X-Test": {"ok"}}, Body: io.NopCloser(strings.NewReader("fixture")), Request: r}, nil
	})
	for i := 0; i < 8; i++ {
		h, b, err := g.GetContext(context.Background(), testCollateralURL)
		if err != nil || string(b) != "fixture" || h["X-Test"][0] != "ok" {
			t.Fatal("valid bounded response rejected")
		}
	}
	if _, _, err := g.Get(testCollateralURL); err == nil || calls != 8 {
		t.Fatal("collateral request budget bypassed")
	}
	g = newCollateralGetter()
	calls = 0
	g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 302, Header: http.Header{"Location": {"https://attacker.invalid/secret"}}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
	})
	if _, _, err := g.Get(testCollateralURL); err == nil || calls != 1 {
		t.Fatal("collateral redirect followed")
	}
}

func TestCollateralSizeAndErrors(t *testing.T) {
	for _, body := range []string{"secret-error", strings.Repeat("x", (4<<20)+1)} {
		g := newCollateralGetter()
		g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			if body == "secret-error" {
				return nil, errors.New(body)
			}
			return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
		})
		if _, _, err := g.Get(testCollateralURL); err == nil || strings.Contains(err.Error(), "secret") {
			t.Fatal("collateral error accepted or leaked")
		}
	}
}

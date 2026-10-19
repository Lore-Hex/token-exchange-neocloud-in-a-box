//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"sync"
	"time"
)

type collateralGetter struct {
	client   *http.Client
	mu       sync.Mutex
	requests int
}

func newCollateralGetter() *collateralGetter {
	return &collateralGetter{client: &http.Client{
		Transport: &http.Transport{
			Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
			TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 5 * time.Second,
			MaxResponseHeaderBytes: 64 << 10, DisableKeepAlives: true,
			TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
		},
		Timeout:       10 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

// These are the exact public Intel endpoints used by the pinned verifier.
// Quote-supplied CRL URLs cannot redirect requests to arbitrary origins.
func allowedCollateral(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.RawFragment != "" || u.ForceQuery || u.RawPath != "" || u.Port() != "" || len(raw) > 256 {
		return false
	}
	if u.Host == "certificates.trustedservices.intel.com" {
		return u.Path == "/IntelSGXRootCA.der" && u.RawQuery == ""
	}
	if u.Host != "api.trustedservices.intel.com" {
		return false
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return false
	}
	switch u.Path {
	case "/tdx/certification/v4/qe/identity":
		return u.RawQuery == ""
	case "/tdx/certification/v4/tcb":
		if len(q) != 1 || len(q["fmspc"]) != 1 {
			return false
		}
		b, err := hex.DecodeString(q.Get("fmspc"))
		return err == nil && len(b) == 6
	case "/sgx/certification/v4/pckcrl":
		return len(q) == 2 && len(q["ca"]) == 1 && len(q["encoding"]) == 1 && (q.Get("ca") == "platform" || q.Get("ca") == "processor") && q.Get("encoding") == "der"
	default:
		return false
	}
}

func (g *collateralGetter) Get(raw string) (map[string][]string, []byte, error) {
	return g.GetContext(context.Background(), raw)
}

func (g *collateralGetter) GetContext(ctx context.Context, raw string) (map[string][]string, []byte, error) {
	if !allowedCollateral(raw) {
		return nil, nil, errors.New("collateral URL is not approved")
	}
	g.mu.Lock()
	g.requests++
	allowed := g.requests <= 8
	g.mu.Unlock()
	if !allowed {
		return nil, nil, errors.New("collateral request budget exceeded")
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	r, err := http.NewRequestWithContext(ctx, http.MethodGet, raw, nil)
	if err != nil {
		return nil, nil, errors.New("invalid collateral request")
	}
	resp, err := g.client.Do(r)
	if err != nil {
		return nil, nil, errors.New("collateral retrieval failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, nil, errors.New("collateral server did not return success")
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, (4<<20)+1))
	if err != nil || len(body) > 4<<20 {
		return nil, nil, errors.New("collateral body is invalid or oversized")
	}
	return resp.Header, body, nil
}

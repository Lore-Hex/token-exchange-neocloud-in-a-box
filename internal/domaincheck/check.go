// SPDX-License-Identifier: Apache-2.0

// Package domaincheck performs system-resolver address-resolution checks only.
// It does not connect to resolved hosts or verify authoritative delegation,
// DNSSEC, inference or attestation.
package domaincheck

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"strings"
	"time"
)

const (
	defaultTimeout = 5 * time.Second
	maxTimeout     = 30 * time.Second
	missingAddress = "missing-address"
	resolved       = "resolved"
	lookupError    = "lookup-error"
	assurance      = "System-resolver address resolution only; authoritative delegation, DNSSEC, inference and attestation are not checked."
)

// Resolver implementations must honor context cancellation. net.Resolver
// implements this interface; injected resolvers keep tests independent of DNS.
type Resolver interface {
	LookupNetIP(context.Context, string, string) ([]netip.Addr, error)
}

type Result struct {
	Name         string `json:"name"`
	Status       string `json:"status"`
	AddressCount int    `json:"address_count"`
}

type Report struct {
	Scope     string    `json:"scope"`
	Assurance string    `json:"assurance"`
	Checks    [2]Result `json:"checks"`
}

// Check validates the domain and api.<domain> before any lookup. Both lookups
// share one timeout, capped at 30 seconds and further bounded by ctx. Resolver
// errors and addresses never escape through the report or returned error.
func Check(ctx context.Context, resolver Resolver, domain string, timeout time.Duration) (Report, error) {
	if !validHostname(domain) || !validHostname("api."+domain) {
		return Report{}, errors.New("domain must be a canonical lowercase ASCII hostname with room for api prefix")
	}
	if timeout <= 0 || timeout > maxTimeout {
		return Report{}, errors.New("timeout must be greater than zero and at most 30s")
	}
	if resolver == nil {
		return Report{}, errors.New("DNS resolver is required")
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	report := Report{
		Scope:     "dns-only",
		Assurance: assurance,
	}
	for i, name := range [2]string{domain, "api." + domain} {
		result := Result{Name: name, Status: lookupError}
		if ctx.Err() == nil {
			// The trailing root label prevents resolver search-domain expansion;
			// network "ip" asks for both IPv4 and IPv6 addresses.
			addresses, err := resolver.LookupNetIP(ctx, "ip", name+".")
			var dnsErr *net.DNSError
			switch {
			case ctx.Err() != nil:
			case err == nil:
				result.Status = missingAddress
				if len(addresses) > 0 {
					result.Status, result.AddressCount = resolved, len(addresses)
				}
			case errors.As(err, &dnsErr) && dnsErr.IsNotFound:
				result.Status = missingAddress
			}
		}
		report.Checks[i] = result
	}
	return report, nil
}

func validHostname(name string) bool {
	if len(name) > 253 || !strings.Contains(name, ".") || name == "localhost" || strings.HasSuffix(name, ".localhost") {
		return false
	}
	if _, err := netip.ParseAddr(name); err == nil {
		return false
	}
	labels := strings.Split(name, ".")
	for _, label := range labels {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, c := range label {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
	}
	// A numeric top-level label can be interpreted as an IP literal by some
	// resolvers, including legacy IPv4 spellings. Require a DNS name instead.
	return strings.ContainsAny(labels[len(labels)-1], "abcdefghijklmnopqrstuvwxyz")
}

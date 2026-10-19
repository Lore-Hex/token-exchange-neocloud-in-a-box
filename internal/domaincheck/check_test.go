// SPDX-License-Identifier: Apache-2.0

package domaincheck

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"reflect"
	"strings"
	"testing"
	"time"
)

type resolverFunc func(context.Context, string, string) ([]netip.Addr, error)

func (f resolverFunc) LookupNetIP(ctx context.Context, network, name string) ([]netip.Addr, error) {
	return f(ctx, network, name)
}

func TestCheckResults(t *testing.T) {
	v4, v6 := netip.MustParseAddr("198.51.100.42"), netip.MustParseAddr("2001:db8::42")
	for _, tc := range []struct {
		name      string
		addresses []netip.Addr
		err       error
		status    string
		count     int
	}{
		{"ipv4", []netip.Addr{v4}, nil, resolved, 1},
		{"ipv6", []netip.Addr{v6}, nil, resolved, 1},
		{"both-families", []netip.Addr{v4, v6}, nil, resolved, 2},
		{"no-addresses", nil, nil, missingAddress, 0},
		{"nxdomain", nil, &net.DNSError{IsNotFound: true, Err: "private resolver message"}, missingAddress, 0},
		{"wrapped-nxdomain", nil, fmt.Errorf("private: %w", &net.DNSError{IsNotFound: true}), missingAddress, 0},
		{"servfail", nil, &net.DNSError{IsTemporary: true, Err: "private resolver message"}, lookupError, 0},
		{"other-error", nil, errors.New("private resolver message"), lookupError, 0},
		{"partial-error", []netip.Addr{v4}, errors.New("private resolver message"), lookupError, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var names []string
			var sharedDeadline time.Time
			resolver := resolverFunc(func(ctx context.Context, network, name string) ([]netip.Addr, error) {
				if network != "ip" {
					t.Fatalf("network=%q", network)
				}
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > time.Second {
					t.Fatal("lookup lacks bounded deadline")
				}
				if !sharedDeadline.IsZero() && !deadline.Equal(sharedDeadline) {
					t.Fatal("lookups do not share deadline")
				}
				sharedDeadline = deadline
				names = append(names, name)
				return tc.addresses, tc.err
			})
			report, err := Check(context.Background(), resolver, "example.invalid", time.Second)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(names, []string{"example.invalid.", "api.example.invalid."}) {
				t.Fatalf("queries=%v", names)
			}
			for i, result := range report.Checks {
				if result.Name != strings.TrimSuffix(names[i], ".") || result.Status != tc.status || result.AddressCount != tc.count {
					t.Fatalf("result=%+v", result)
				}
			}
			if report.Scope != "dns-only" || report.Assurance != "System-resolver address resolution only; authoritative delegation, DNSSEC, inference and attestation are not checked." {
				t.Fatalf("assurance=%+v", report)
			}
		})
	}
}

func TestInvalidInputsDoNotResolve(t *testing.T) {
	resolver := resolverFunc(func(context.Context, string, string) ([]netip.Addr, error) {
		t.Fatal("invalid input reached DNS")
		return nil, nil
	})
	for _, domain := range []string{
		"", "localhost", "api.localhost", "com", "EXAMPLE.invalid", "example.Invalid", " example.invalid", "example.invalid\n",
		"https://example.invalid", "example.invalid/path", "example.invalid:443", "user@example.invalid", "example.invalid?query",
		"127.0.0.1", "127.0.0.01", "::1", "[::1]", "_api.example.invalid", "example.invalid.", ".example.invalid", "example..invalid",
		"-bad.invalid", "bad-.invalid", "ex\x00ample.invalid", "ex\u00e4mple.invalid", "*.example.invalid", strings.Repeat("a", 64) + ".invalid",
	} {
		t.Run(domain, func(t *testing.T) {
			if _, err := Check(context.Background(), resolver, domain, time.Second); err == nil {
				t.Fatal("invalid domain accepted")
			}
		})
	}
	for _, timeout := range []time.Duration{-time.Second, 0, maxTimeout + 1} {
		if _, err := Check(context.Background(), resolver, "example.invalid", timeout); err == nil {
			t.Fatal("invalid timeout accepted")
		}
	}
	if _, err := Check(context.Background(), nil, "example.invalid", time.Second); err == nil {
		t.Fatal("nil resolver accepted")
	}
}

func TestGeneratedHostnameLength(t *testing.T) {
	boundary := strings.Repeat("a", 63) + "." + strings.Repeat("b", 63) + "." + strings.Repeat("c", 63) + "." + strings.Repeat("d", 57)
	calls := 0
	resolver := resolverFunc(func(_ context.Context, _, name string) ([]netip.Addr, error) {
		calls++
		if len(name) > 254 {
			t.Fatal("absolute query exceeds DNS name bound")
		}
		return nil, nil
	})
	if _, err := Check(context.Background(), resolver, boundary, maxTimeout); err != nil || calls != 2 {
		t.Fatalf("boundary err=%v calls=%d", err, calls)
	}
	if _, err := Check(context.Background(), resolver, boundary+"d", time.Second); err == nil || calls != 2 {
		t.Fatal("overlong generated api name reached DNS")
	}
}

func TestTimeoutAndCancellation(t *testing.T) {
	for _, parentDeadline := range []bool{false, true} {
		t.Run(fmt.Sprint(parentDeadline), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			timeout := 20 * time.Millisecond
			if parentDeadline {
				cancel()
				ctx, cancel = context.WithTimeout(context.Background(), timeout)
				timeout = maxTimeout
			}
			defer cancel()
			calls := 0
			resolver := resolverFunc(func(ctx context.Context, _, _ string) ([]netip.Addr, error) {
				calls++
				<-ctx.Done()
				// Even an apparent late success must not turn a timeout into success.
				return []netip.Addr{netip.MustParseAddr("198.51.100.42")}, nil
			})
			start := time.Now()
			report, err := Check(ctx, resolver, "example.invalid", timeout)
			if err != nil || time.Since(start) > time.Second || calls != 1 {
				t.Fatalf("timeout err=%v calls=%d", err, calls)
			}
			for _, result := range report.Checks {
				if result.Status != lookupError || result.AddressCount != 0 {
					t.Fatalf("timeout result=%+v", result)
				}
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	resolver := resolverFunc(func(context.Context, string, string) ([]netip.Addr, error) {
		t.Fatal("canceled context reached DNS")
		return nil, nil
	})
	report, err := Check(ctx, resolver, "example.invalid", time.Second)
	if err != nil || report.Checks[0].Status != lookupError || report.Checks[1].Status != lookupError {
		t.Fatalf("cancellation=%+v err=%v", report, err)
	}
}

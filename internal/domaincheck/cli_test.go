// SPDX-License-Identifier: Apache-2.0

package domaincheck

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/netip"
	"strings"
	"testing"
	"time"
)

func TestCLIStatusesAndPrivacy(t *testing.T) {
	for _, tc := range []struct {
		name, apex, api string
		code            int
	}{
		{"resolved", resolved, resolved, 0},
		{"both-missing", missingAddress, missingAddress, 1},
		{"api-missing", resolved, missingAddress, 1},
		{"apex-missing", missingAddress, resolved, 1},
		{"lookup-error", lookupError, resolved, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			resolver := resolverFunc(func(ctx context.Context, _, name string) ([]netip.Addr, error) {
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > defaultTimeout {
					t.Fatal("unbounded CLI default")
				}
				status := tc.apex
				if name == "api.example.invalid." {
					status = tc.api
				}
				switch status {
				case resolved:
					return []netip.Addr{netip.MustParseAddr("198.51.100.42"), netip.MustParseAddr("2001:db8::42")}, nil
				case missingAddress:
					return nil, &net.DNSError{IsNotFound: true, Name: "private-query", Server: "198.51.100.53", Err: "private-error"}
				default:
					return nil, errors.New("private-error 198.51.100.53 private-query")
				}
			})
			var out, errs bytes.Buffer
			if code := run(context.Background(), []string{"--domain", "example.invalid"}, resolver, &out, &errs); code != tc.code {
				t.Fatalf("code=%d", code)
			}
			var report Report
			if err := json.Unmarshal(out.Bytes(), &report); err != nil {
				t.Fatal(err)
			}
			for i, status := range []string{tc.apex, tc.api} {
				count := 0
				if status == resolved {
					count = 2
				}
				if report.Checks[i].Status != status || report.Checks[i].AddressCount != count {
					t.Fatalf("report=%+v", report)
				}
			}
			for _, private := range []string{"198.51.100.", "2001:db8:", "private-query", "private-error"} {
				if strings.Contains(out.String()+errs.String(), private) {
					t.Fatal("private DNS material escaped")
				}
			}
			if errs.Len() != 0 || !strings.HasSuffix(out.String(), "\n") {
				t.Fatal("unexpected CLI output")
			}
		})
	}
}

func TestCLIInvalidArgumentsAndHelp(t *testing.T) {
	resolver := resolverFunc(func(context.Context, string, string) ([]netip.Addr, error) {
		t.Fatal("invalid arguments reached DNS")
		return nil, nil
	})
	for _, args := range [][]string{
		nil, {"--domain"}, {"--domain", ""}, {"--domain", "https://private-canary.invalid"},
		{"--unknown=private-canary"}, {"--domain", "example.invalid", "private-canary"},
		{"--domain", "example.invalid", "--timeout", "0s"}, {"--domain", "example.invalid", "--timeout", "31s"},
		{"--domain", "example.invalid", "--timeout", "-1s"}, {"--domain", "example.invalid", "--timeout", "private-canary"},
	} {
		var out, errs bytes.Buffer
		if code := run(context.Background(), args, resolver, &out, &errs); code != 2 || out.Len() != 0 || errs.Len() == 0 || strings.Contains(errs.String(), "private-canary") {
			t.Fatalf("invalid argument handling: code=%d out=%q errs=%q", code, out.String(), errs.String())
		}
	}
	var out, errs bytes.Buffer
	if code := Run(context.Background(), []string{"--help"}, &out, &errs); code != 0 || out.String() != usage || errs.Len() != 0 {
		t.Fatal("help failed")
	}
	if code := Run(context.Background(), nil, io.Discard, io.Discard); code != 2 {
		t.Fatal("domain was not required")
	}
}

type failingWriter struct{ short bool }

func (w failingWriter) Write(p []byte) (int, error) {
	if w.short {
		return len(p) - 1, nil
	}
	return 0, errors.New("private-output-error 198.51.100.53")
}

func TestCLIOutputFailures(t *testing.T) {
	resolver := resolverFunc(func(context.Context, string, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("198.51.100.42")}, nil
	})
	for _, args := range [][]string{{"--domain", "example.invalid"}, {"--help"}} {
		for _, short := range []bool{false, true} {
			var errs bytes.Buffer
			if code := run(context.Background(), args, resolver, failingWriter{short: short}, &errs); code != 1 || errs.String() != "nib-domain-check: cannot write output\n" {
				t.Fatalf("output failure: code=%d errs=%q", code, errs.String())
			}
		}
	}
	if code := run(context.Background(), nil, resolver, io.Discard, failingWriter{}); code == 0 {
		t.Fatal("diagnostic failure returned success")
	}
}

func TestCLITimeout(t *testing.T) {
	resolver := resolverFunc(func(ctx context.Context, _, _ string) ([]netip.Addr, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	})
	var out, errs bytes.Buffer
	if code := run(context.Background(), []string{"--domain", "example.invalid", "--timeout", "20ms"}, resolver, &out, &errs); code != 1 {
		t.Fatalf("timeout code=%d", code)
	}
	var report Report
	if err := json.Unmarshal(out.Bytes(), &report); err != nil {
		t.Fatal(err)
	}
	for _, result := range report.Checks {
		if result.Status != lookupError || result.AddressCount != 0 {
			t.Fatalf("timeout=%+v", result)
		}
	}
}

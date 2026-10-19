// SPDX-License-Identifier: Apache-2.0
package nftables

import (
	"strings"
	"testing"
)

func TestPolicy(t *testing.T) {
	a, err := Render([]string{"2001:db8::1", "192.0.2.2", "192.0.2.1"})
	if err != nil {
		t.Fatal(err)
	}
	b, err := Render([]string{"192.0.2.1", "192.0.2.2", "2001:db8::1"})
	if err != nil || string(a) != string(b) {
		t.Fatal("non-deterministic policy")
	}
	s := string(a)
	for _, required := range []string{
		"hook input priority 0; policy drop;", "hook output priority 0; policy drop;", "hook forward priority 0; policy drop;",
		"tcp dport 8443 ct state new accept", "ip daddr @egress4 tcp dport 443 ct state new accept", "ip6 daddr @egress6 tcp dport 443 ct state new accept",
		"elements = { 192.0.2.1, 192.0.2.2 };", "elements = { 2001:db8::1 };",
	} {
		if !strings.Contains(s, required) {
			t.Fatalf("missing restriction: %s", required)
		}
	}
	for _, forbidden := range []string{"flush ruleset", "policy accept", " dport 22", " dport 8000", " dport 53", " log ", "IPV4_ELEMENTS", "IPV6_ELEMENTS"} {
		if strings.Contains(s, forbidden) {
			t.Fatalf("unexpected policy element: %s", forbidden)
		}
	}
	empty, err := Render(nil)
	if err != nil || strings.Contains(string(empty), "elements =") {
		t.Fatal("empty allowlist is not empty")
	}
}

func TestRejectUnsafeDestinations(t *testing.T) {
	for _, s := range []string{"example.com", "0.0.0.0", "127.0.0.1", "169.254.169.254", "224.0.0.1", "255.255.255.255", "::", "::1", "fe80::1", "fe80::1%eth0", "::ffff:192.0.2.1", "192.0.2.1/24", "192.0.2.1; accept", "192.0.2.1\nflush ruleset"} {
		if _, err := Render([]string{s}); err == nil {
			t.Fatalf("unsafe destination accepted: %q", s)
		}
	}
	if _, err := Render([]string{"192.0.2.1", "192.0.2.1"}); err == nil {
		t.Fatal("duplicate accepted")
	}
	if _, err := Render(make([]string, 33)); err == nil {
		t.Fatal("unbounded destinations")
	}
}

func TestExactRuleSurface(t *testing.T) {
	b, err := Render(nil)
	if err != nil {
		t.Fatal(err)
	}
	var rules []string
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		rules = append(rules, line)
	}
	want := `table inet nib {
set egress4 {
type ipv4_addr;
}
set egress6 {
type ipv6_addr;
}
chain input {
type filter hook input priority 0; policy drop;
iifname "lo" accept
ct state invalid drop
ct state established,related accept
tcp dport 8443 ct state new accept
}
chain forward {
type filter hook forward priority 0; policy drop;
}
chain output {
type filter hook output priority 0; policy drop;
oifname "lo" accept
ct state invalid drop
ct state established,related accept
ip daddr @egress4 tcp dport 443 ct state new accept
ip6 daddr @egress6 tcp dport 443 ct state new accept
}
}`
	if strings.Join(rules, "\n") != want {
		t.Fatal("firewall rule surface changed; review the complete policy")
	}
}

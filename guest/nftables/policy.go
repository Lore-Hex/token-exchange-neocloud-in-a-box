// SPDX-License-Identifier: Apache-2.0

// Package nftables renders a policy; it never installs or changes a firewall.
package nftables

import (
	_ "embed"
	"errors"
	"net/netip"
	"sort"
	"strings"
)

//go:embed nib.nft
var policy string

// Render allows at most 32 literal unicast destinations over TCP 443. No DNS,
// arbitrary ports, CIDRs, shell syntax, logging or replacement rules are accepted.
func Render(destinations []string) ([]byte, error) {
	if len(destinations) > 32 {
		return nil, errors.New("egress destination limit")
	}
	var v4, v6 []string
	seen := make(map[netip.Addr]bool)
	for _, s := range destinations {
		a, err := netip.ParseAddr(s)
		if err != nil || a.Zone() != "" || a.Is4In6() || !a.IsGlobalUnicast() || a.IsLoopback() || a.IsLinkLocalUnicast() || seen[a] {
			return nil, errors.New("invalid egress destination")
		}
		seen[a] = true
		if a.Is4() {
			v4 = append(v4, a.String())
		} else {
			v6 = append(v6, a.String())
		}
	}
	sort.Strings(v4)
	sort.Strings(v6)
	r := strings.NewReplacer("# IPV4_ELEMENTS", elements(v4), "# IPV6_ELEMENTS", elements(v6))
	return []byte(r.Replace(policy)), nil
}

func elements(addresses []string) string {
	if len(addresses) == 0 {
		return ""
	}
	return "elements = { " + strings.Join(addresses, ", ") + " };"
}

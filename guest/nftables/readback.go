// SPDX-License-Identifier: Apache-2.0
package nftables

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/netip"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

const ReadbackLimit = 64 << 10

var errReadback = errors.New("network policy readback rejected")

// Create reuses the candidate policy but refuses an existing table. There is
// deliberately no flush, replace, retry, or deletion on failure.
func Create(destinations []string) ([]byte, error) {
	raw, err := Render(destinations)
	if err != nil {
		return nil, err
	}
	if bytes.Count(raw, []byte("table inet nib {")) != 1 {
		return nil, errReadback
	}
	return bytes.Replace(raw, []byte("table inet nib {"), []byte("create table inet nib {"), 1), nil
}

// Empty requires a complete, empty ruleset, not merely absence of our table.
func Empty(raw []byte) error {
	objects, err := dump(raw)
	if err != nil || len(objects) != 0 {
		return errReadback
	}
	return nil
}

// Verify checks the entire kernel ruleset against the rendered policy. Only
// allocated handles and unordered set/table/chain enumeration are normalized;
// rule order within each chain and every expression/field remain exact.
// nftables 1.0.9: src/json.c, doc/libnftables-json.adoc, tests/py/any/ct.t.json.
func Verify(raw []byte, destinations []string) error {
	if _, err := Render(destinations); err != nil {
		return errReadback
	}
	objects, err := dump(raw)
	if err != nil || len(objects) != 15 {
		return errReadback
	}
	actual := map[string]any{}
	for _, item := range objects {
		wrapper, ok := item.(map[string]any)
		if !ok || len(wrapper) != 1 {
			return errReadback
		}
		for kind, value := range wrapper {
			obj, ok := value.(map[string]any)
			if !ok {
				return errReadback
			}
			handle, ok := obj["handle"].(json.Number)
			n, err := strconv.ParseUint(string(handle), 10, 64)
			if !ok || err != nil || n == 0 || strconv.FormatUint(n, 10) != string(handle) {
				return errReadback
			}
			delete(obj, "handle")
			key := kind
			switch kind {
			case "table":
			case "set", "chain":
				name, ok := obj["name"].(string)
				if !ok {
					return errReadback
				}
				key += "/" + name
				if kind == "set" {
					if elems, present := obj["elem"]; present {
						values, ok := elems.([]any)
						if !ok || len(values) == 0 || len(values) > 32 {
							return errReadback
						}
						addresses := make([]string, len(values))
						for i, value := range values {
							if addresses[i], ok = value.(string); !ok {
								return errReadback
							}
						}
						sort.Strings(addresses)
						obj["elem"] = addresses
					}
				}
			case "rule":
				chain, ok := obj["chain"].(string)
				if !ok {
					return errReadback
				}
				key += "/" + chain
				rules, _ := actual[key].([]any)
				actual[key] = append(rules, obj)
				continue
			default:
				return errReadback
			}
			if _, exists := actual[key]; exists {
				return errReadback
			}
			actual[key] = obj
		}
	}
	if !reflect.DeepEqual(actual, expected(destinations)) {
		return errReadback
	}
	return nil
}

func expected(destinations []string) map[string]any {
	result := map[string]any{"table": map[string]any{"family": "inet", "name": "nib"}}
	for _, family := range []string{"4", "6"} {
		set := map[string]any{"family": "inet", "table": "nib", "name": "egress" + family, "type": "ipv" + family + "_addr"}
		var addresses []string
		for _, raw := range destinations {
			a := netip.MustParseAddr(raw) // Render has already validated every address.
			if a.Is4() == (family == "4") {
				addresses = append(addresses, a.String())
			}
		}
		if len(addresses) != 0 {
			sort.Strings(addresses)
			set["elem"] = addresses
		}
		result["set/egress"+family] = set
	}
	for _, chain := range []string{"input", "forward", "output"} {
		result["chain/"+chain] = map[string]any{"family": "inet", "table": "nib", "name": chain,
			"type": "filter", "hook": chain, "prio": json.Number("0"), "policy": "drop"}
	}
	match := func(left any, op string, right any) any {
		return map[string]any{"match": map[string]any{"left": left, "op": op, "right": right}}
	}
	field := func(kind, key string) any { return map[string]any{kind: map[string]any{"key": key}} }
	payload := func(protocol, field string) any {
		return map[string]any{"payload": map[string]any{"protocol": protocol, "field": field}}
	}
	state := func(right any) any { return match(field("ct", "state"), "in", right) }
	port := func(n string) any { return match(payload("tcp", "dport"), "==", json.Number(n)) }
	add := func(chain, verdict string, expressions ...any) {
		key := "rule/" + chain
		rules, _ := result[key].([]any)
		expressions = append(expressions, map[string]any{verdict: nil})
		result[key] = append(rules, map[string]any{"family": "inet", "table": "nib", "chain": chain, "expr": expressions})
	}
	for _, pair := range [][2]string{{"input", "iifname"}, {"output", "oifname"}} {
		add(pair[0], "accept", match(field("meta", pair[1]), "==", "lo"))
		add(pair[0], "drop", state("invalid"))
		add(pair[0], "accept", state([]any{"established", "related"}))
	}
	add("input", "accept", port("8443"), state("new"))
	add("output", "accept", match(payload("ip", "daddr"), "==", "@egress4"), port("443"), state("new"))
	add("output", "accept", match(payload("ip6", "daddr"), "==", "@egress6"), port("443"), state("new"))
	return result
}

func dump(raw []byte) ([]any, error) {
	value, err := Decode(raw)
	if err != nil {
		return nil, errReadback
	}
	root, ok := value.(map[string]any)
	if !ok || len(root) != 1 {
		return nil, errReadback
	}
	objects, ok := root["nftables"].([]any)
	meta := map[string]any{"metainfo": map[string]any{"version": "1.0.9",
		"release_name": "Old Doc Yak #3", "json_schema_version": json.Number("1")}}
	if !ok || len(objects) == 0 || !reflect.DeepEqual(objects[0], meta) {
		return nil, errReadback
	}
	return objects[1:], nil
}

// Decode bounds JSON structure and rejects duplicate keys and trailing data.
// Verdict nulls are valid nft JSON; policy/config validators narrow the result.
func Decode(raw []byte) (any, error) {
	if len(raw) == 0 || len(raw) > ReadbackLimit || !utf8.Valid(raw) {
		return nil, errReadback
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > 16 {
			return nil, errReadback
		}
		t, err := d.Token()
		if err != nil {
			return nil, errReadback
		}
		switch t {
		case json.Delim('{'):
			m := map[string]any{}
			for d.More() {
				key, err := d.Token()
				s, ok := key.(string)
				if err != nil || !ok {
					return nil, errReadback
				}
				if _, exists := m[s]; exists {
					return nil, errReadback
				}
				v, err := read(depth + 1)
				if err != nil {
					return nil, err
				}
				m[s] = v
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return nil, errReadback
			}
			return m, nil
		case json.Delim('['):
			list := []any{}
			for d.More() {
				v, err := read(depth + 1)
				if err != nil {
					return nil, err
				}
				list = append(list, v)
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return nil, errReadback
			}
			return list, nil
		default:
			if _, delimiter := t.(json.Delim); delimiter {
				return nil, errReadback
			}
			return t, nil
		}
	}
	value, err := read(0)
	if err != nil {
		return nil, err
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, errReadback
	}
	return value, nil
}

// Config is deliberately distinct from the compile-time-only fake schema.
func Config(raw []byte) ([]string, error) {
	if len(raw) > 4096 {
		return nil, errReadback
	}
	value, err := Decode(raw)
	if err != nil {
		return nil, err
	}
	m, ok := value.(map[string]any)
	if !ok || len(m) != 2 || m["schema"] != "nib.guest-network/v1" {
		return nil, errReadback
	}
	items, ok := m["egress"].([]any)
	if !ok || len(items) > 32 {
		return nil, errReadback
	}
	destinations := make([]string, len(items))
	for i, item := range items {
		if destinations[i], ok = item.(string); !ok || strings.TrimSpace(destinations[i]) != destinations[i] {
			return nil, errReadback
		}
	}
	if _, err := Render(destinations); err != nil {
		return nil, errReadback
	}
	return destinations, nil
}

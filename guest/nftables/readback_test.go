// SPDX-License-Identifier: Apache-2.0
package nftables

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func readbackFixture(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile("testdata/readback.json")
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestCreateAndStrictConfig(t *testing.T) {
	for _, raw := range []string{`{"schema":"nib.guest-network/v1","egress":[]}`,
		`{"egress":["192.0.2.1","2001:db8::1"],"schema":"nib.guest-network/v1"}`} {
		destinations, err := Config([]byte(raw))
		if err != nil {
			t.Fatal(err)
		}
		rendered, _ := Render(destinations)
		created, err := Create(destinations)
		if err != nil || bytes.Count(created, []byte("create table inet nib {")) != 1 ||
			!bytes.Equal(bytes.Replace(created, []byte("create table"), []byte("table"), 1), rendered) {
			t.Fatal("create did not preserve rendered policy")
		}
	}
	for _, raw := range []string{"", `{}`, `null`, `[]`, `{"schema":"nib.guest-network/v1","egress":null}`,
		`{"schema":"nib.guest-network/v1","schema":"nib.guest-network/v1","egress":[]}`,
		`{"Schema":"nib.guest-network/v1","egress":[]}`, `{"schema":"nib.guest-fake/v1","egress":[]}`,
		`{"schema":"nib.guest-network/v1","egress":[],"applied":true}`,
		`{"schema":"nib.guest-network/v1","egress":[1]}`, `{"schema":"nib.guest-network/v1","egress":["127.0.0.1"]}`,
		`{"schema":"nib.guest-network/v1","egress":["example.invalid"]}`, `{"schema":"nib.guest-network/v1","egress":[]} {}`,
		strings.Repeat(" ", 4097)} {
		if _, err := Config([]byte(raw)); err == nil {
			t.Fatal("accepted invalid configuration")
		}
	}
}

func TestSourceBackedReadbackShape(t *testing.T) {
	raw := readbackFixture(t)
	if err := Verify(raw, nil); err != nil {
		t.Fatal(err)
	}
	// nft1.0.9 tests/py/any/ct.t.json:1-19 vs36-56 distinguishes
	// comma-list bitmask from brace-set syntax; our renderer uses no braces.
	if bytes.Contains(raw, []byte(`"right":{"set"`)) ||
		!bytes.Contains(raw, []byte(`"right":["established","related"]`)) {
		t.Fatal("fixture lost comma-list representation")
	}
	rendered, _ := Render(nil)
	if bytes.Count(rendered, []byte("ct state established,related accept")) != 2 {
		t.Fatal("policy changed conntrack syntax")
	}
	wrapped := bytes.Replace(raw, []byte(`"right":["established","related"]`),
		[]byte(`"right":{"set":["established","related"]}`), 1)
	if Verify(wrapped, nil) == nil {
		t.Fatal("accepted unrequested set expression")
	}
	value, err := Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	m := value.(map[string]any)
	objects := m["nftables"].([]any)
	empty, _ := json.Marshal(map[string]any{"nftables": objects[:1]})
	if Empty(empty) != nil || Empty(raw) == nil || Verify(empty, nil) == nil {
		t.Fatal("empty and installed state confused")
	}
	objects[2], objects[3] = objects[3], objects[2]
	objects[4], objects[6] = objects[6], objects[4]
	for _, item := range objects[1:] {
		for _, obj := range item.(map[string]any) {
			obj.(map[string]any)["handle"] = json.Number("999")
		}
	}
	reordered, _ := json.Marshal(m)
	if Verify(reordered, nil) != nil {
		t.Fatal("unordered enumeration or allocated handles rejected")
	}
}

func TestReadbackMutationsFailClosed(t *testing.T) {
	raw := readbackFixture(t)
	for _, change := range [][2]string{
		{`"policy":"drop"`, `"policy":"accept"`}, {`"prio":0`, `"prio":-1`},
		{`"right":8443`, `"right":8000`}, {`"right":443`, `"right":53`},
		{`"right":"lo"`, `"right":"eth0"`}, {`"right":"@egress4"`, `"right":"@other"`},
		{`"handle":1`, `"handle":0`}, {`"handle":1`, `"handle":1.0`},
		{`"handle":1`, `"handle":1,"comment":"unknown"`},
		{`"version":"1.0.9"`, `"version":"1.1.0"`},
		{`"json_schema_version":1`, `"json_schema_version":2`},
		{`"family":"inet"`, `"family":"ip"`}, {`"accept":null`, `"drop":null`},
		{`"right":"invalid"`, `"right":1`}, {`"right":"new"`, `"right":"established"`},
		{`"type":"ipv4_addr"`, `"type":"ipv4_addr","flags":["interval"]`},
		{`"op":"=="`, `"op":"!=","op":"=="`},
	} {
		mutated := bytes.Replace(raw, []byte(change[0]), []byte(change[1]), 1)
		if bytes.Equal(mutated, raw) || Verify(mutated, nil) == nil {
			t.Fatal("mutation was absent or accepted")
		}
	}
	value, _ := Decode(raw)
	m := value.(map[string]any)
	objects := m["nftables"].([]any)
	for _, changed := range [][]any{objects[:len(objects)-1], append(append([]any{}, objects...), objects[1])} {
		mutated, _ := json.Marshal(map[string]any{"nftables": changed})
		if Verify(mutated, nil) == nil {
			t.Fatal("missing/extra object accepted")
		}
	}
	objects[8], objects[9] = objects[9], objects[8]
	mutated, _ := json.Marshal(m)
	if Verify(mutated, nil) == nil {
		t.Fatal("changed rule order accepted")
	}
}

func TestReadbackExactDestinations(t *testing.T) {
	raw := readbackFixture(t)
	raw = bytes.Replace(raw, []byte(`"type":"ipv4_addr"`),
		[]byte(`"type":"ipv4_addr","elem":["192.0.2.2","192.0.2.1"]`), 1)
	raw = bytes.Replace(raw, []byte(`"type":"ipv6_addr"`),
		[]byte(`"type":"ipv6_addr","elem":["2001:db8::1"]`), 1)
	destinations := []string{"192.0.2.1", "2001:db8::1", "192.0.2.2"}
	if Verify(raw, destinations) != nil || Verify(raw, destinations[:2]) == nil || Verify(raw, nil) == nil {
		t.Fatal("destination equality not enforced")
	}
	duplicate := bytes.Replace(raw, []byte(`"192.0.2.2"`), []byte(`"192.0.2.1"`), 1)
	if Verify(duplicate, destinations) == nil {
		t.Fatal("duplicate element accepted")
	}
}

func TestStrictDecodeBounds(t *testing.T) {
	for _, raw := range [][]byte{nil, []byte(`{"a":1,"a":2}`), []byte(`{"nested":{"a":1,"a":2}}`),
		[]byte(`{} []`), []byte(`{"a":NaN}`), []byte(strings.Repeat("[", 18) + "0" + strings.Repeat("]", 18)),
		bytes.Repeat([]byte(" "), ReadbackLimit+1), {0xff}} {
		if _, err := Decode(raw); err == nil {
			t.Fatal("invalid JSON accepted")
		}
	}
	value, err := Decode([]byte(`{"accept":null}`))
	if err != nil || !reflect.DeepEqual(value, map[string]any{"accept": nil}) {
		t.Fatal("valid verdict null rejected")
	}
}

// SPDX-License-Identifier: Apache-2.0

package config

import (
	"strings"
	"testing"
)

func TestManifestRules(t *testing.T) {
	valid := string(fixture(t, "valid/files.sha256"))
	if err := ValidateFileManifest([]byte(valid), 3); err != nil {
		t.Fatal(err)
	}
	files, err := ParseFileManifest([]byte(valid), 3)
	if err != nil || len(files) != 2 || files[0].Bytes != 1 || files[1].Bytes != 2 || files[0].Path != "a.json" || files[1].Path != "b.json" || len(files[0].SHA256) != 64 {
		t.Fatal("typed manifest differs from validator")
	}
	a := strings.Repeat("a", 64)
	for name, data := range map[string]string{
		"empty": "", "blank-line": valid + "\n", "bad-digest": "bad  3  a.json\n",
		"zero-digest":      strings.Repeat("0", 64) + "  3  a.json\n",
		"uppercase-digest": strings.Repeat("A", 64) + "  3  a.json\n",
		"negative-size":    a + "  -3  a.json\n", "size-overflow": a + "  9223372036854775808  a.json\n",
		"fraction-size": a + "  3.0  a.json\n", "size-plus": a + "  +3  a.json\n", "size-leading-zero": a + "  03  a.json\n",
		"size-max": a + "  9007199254740992  a.json\n", "wrong-total": a + "  2  a.json\n",
		"traversal": a + "  3  ../a.json\n", "absolute": a + "  3  /a.json\n", "dot-segment": a + "  3  x/./a.json\n",
		"windows": a + "  3  x\\a.json\n", "empty-component": a + "  3  x//a.json\n",
		"unsorted": a + "  1  b.json\n" + a + "  2  a.json\n", "duplicate": a + "  1  a.json\n" + a + "  2  a.json\n",
		"single-spaces": a + " 3 a.json\n", "tabs": a + "\t3\ta.json\n", "extra-columns": a + "  3  a.json  extra\n",
		"oversized-line": strings.Repeat("x", 65537),
	} {
		t.Run(name, func(t *testing.T) {
			if err := ValidateFileManifest([]byte(data), 3); err == nil {
				t.Fatal("invalid manifest accepted")
			}
			if files, err := ParseFileManifest([]byte(data), 3); err == nil || files != nil {
				t.Fatal("invalid manifest returned partial entries")
			}
		})
	}
	if err := ValidateFileManifest([]byte(a+"  0  empty.json\n"), 0); err != nil {
		t.Fatal(err)
	}
	if err := ValidateFileManifest([]byte(strings.Repeat(" ", MaxManifestBytes+1)), 0); err == nil {
		t.Fatal("oversized manifest accepted")
	}
}

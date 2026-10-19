// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHelp(t *testing.T) {
	for _, args := range [][]string{nil, {"help"}, {"--help"}} {
		var out, errOut bytes.Buffer
		if code := run(args, &out, &errOut); code != 0 || !strings.Contains(out.String(), "pre-alpha") || errOut.Len() != 0 {
			t.Fatalf("help failed: code=%d out=%q err=%q", code, out.String(), errOut.String())
		}
	}
}

func TestLint(t *testing.T) {
	root := t.TempDir()
	var out, errOut bytes.Buffer
	if code := run([]string{"lint", root}, &out, &errOut); code != 0 || errOut.Len() != 0 || !strings.Contains(out.String(), "no hardware validation") {
		t.Fatalf("lint failed: %d, %s", code, &errOut)
	}
	if err := os.WriteFile(filepath.Join(root, "release.json"), []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	errOut.Reset()
	if code := run([]string{"lint", root}, &out, &errOut); code != 1 || errOut.Len() == 0 || out.Len() != 0 {
		t.Fatal("invalid repository accepted")
	}
	if code := run([]string{"lint", root, "extra"}, &out, &errOut); code != 2 {
		t.Fatal("extra lint argument accepted")
	}
}

func TestUnsupportedDoesNotEchoArguments(t *testing.T) {
	var out, errOut bytes.Buffer
	if code := run([]string{"not-a-command", "private-value"}, &out, &errOut); code != 2 {
		t.Fatalf("got %d", code)
	}
	if strings.Contains(out.String()+errOut.String(), "private-value") {
		t.Fatal("echoed a command argument")
	}
}

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

func TestEvidenceDispatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "deny")
	if err := os.WriteFile(path, []byte("planted-cli-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	code := runWithInput([]string{"evidence", "scrub", "--deny-file", path}, strings.NewReader("status planted-cli-token"), &out, &errOut)
	if code != 0 || out.String() != "status [REDACTED]" || errOut.Len() != 0 {
		t.Fatal("evidence scrub dispatch failed")
	}
	out.Reset()
	errOut.Reset()
	if code := run([]string{"evidence", "--help"}, &out, &errOut); code != 0 || !strings.Contains(out.String(), "evidence new") || errOut.Len() != 0 {
		t.Fatal("evidence help dispatch failed")
	}
}

func TestEvidenceFailureDoesNotEchoArguments(t *testing.T) {
	var out, errOut bytes.Buffer
	code := runWithInput([]string{"evidence", "scrub", "--deny-file", "/missing/private-canary"}, strings.NewReader("private-input-canary"), &out, &errOut)
	if code != 1 || out.Len() != 0 || strings.Contains(errOut.String(), "canary") {
		t.Fatal("evidence failure disclosed sensitive input")
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

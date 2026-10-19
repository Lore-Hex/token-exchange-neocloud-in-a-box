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
		for _, command := range []string{"nib verify ", "nib catalog ", "nib model check-remote ", "nib model check-local "} {
			if !strings.Contains(out.String(), command) {
				t.Fatalf("help omitted %q", command)
			}
		}
	}
}

func TestVerifyDispatch(t *testing.T) {
	var out, errs bytes.Buffer
	if code := run([]string{"verify"}, &out, &errs); code != 2 || out.Len() != 0 || !strings.Contains(errs.String(), "usage: nib verify") {
		t.Fatal("verify usage dispatch failed")
	}
	errs.Reset()
	args := []string{"verify", "--endpoint", "https://example.invalid", "--release", "/missing/private-canary", "--release-sha256", strings.Repeat("0", 64), "--profile", "/missing/private-canary", "--profile-sha256", strings.Repeat("0", 64)}
	if code := run(args, &out, &errs); code != 1 || out.Len() != 0 || !strings.Contains(errs.String(), "cannot open policy file") || strings.Contains(errs.String(), "canary") {
		t.Fatal("verify failure dispatch or redaction failed")
	}
}

func TestModelDispatch(t *testing.T) {
	var out, errs bytes.Buffer
	if code := run([]string{"model", "--help"}, &out, &errs); code != 0 || !strings.Contains(out.String(), "check-remote") {
		t.Fatal("model help dispatch failed")
	}
	out.Reset()
	if code := run([]string{"model", "check-remote", "/missing/private-canary"}, &out, &errs); code != 1 || out.Len() != 0 || strings.Contains(errs.String(), "canary") {
		t.Fatal("model failure dispatch or redaction failed")
	}
	errs.Reset()
	if code := run([]string{"model", "check-local", "/missing/private-canary", "/missing/staged-canary"}, &out, &errs); code != 1 || out.Len() != 0 || strings.Contains(errs.String(), "canary") {
		t.Fatal("local model failure dispatch or redaction failed")
	}
}

func TestCatalogDispatch(t *testing.T) {
	var out, errs bytes.Buffer
	if code := run([]string{"catalog", "--help"}, &out, &errs); code != 0 || !strings.Contains(out.String(), "local-test-only") || errs.Len() != 0 {
		t.Fatal("catalog help dispatch failed")
	}
	out.Reset()
	if code := run([]string{"catalog", "--root", "../.."}, &out, &errs); code != 0 || errs.Len() != 0 {
		t.Fatal("catalog generation dispatch failed")
	}
	want, err := os.ReadFile("../../catalog/testdata/catalog.v2.golden.json")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out.Bytes(), want) {
		t.Fatal("catalog CLI differs from validated golden declaration")
	}
	out.Reset()
	if code := run([]string{"catalog", "--root", "/missing/private-canary"}, &out, &errs); code != 1 || out.Len() != 0 || strings.Contains(errs.String(), "canary") {
		t.Fatal("catalog error dispatch or redaction failed")
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

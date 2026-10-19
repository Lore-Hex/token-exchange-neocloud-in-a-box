// SPDX-License-Identifier: Apache-2.0
package evidence

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunUsageDoesNotEchoArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"private-argument-canary"}, {"scrub", "--token", "private-argument-canary"}, {"new", "P0", "safe", "private-argument-canary"}} {
		var out, stderr bytes.Buffer
		if code := Run(args, strings.NewReader("private-input-canary"), &out, &stderr); code != 2 {
			t.Fatal("expected usage failure")
		}
		if out.Len() != 0 || strings.Contains(stderr.String(), "canary") {
			t.Fatal("argument or input echoed")
		}
	}
}

func TestRunScrubPrivateFiles(t *testing.T) {
	root := t.TempDir()
	deny := filepath.Join(root, "private-path-canary")
	if err := os.WriteFile(deny, []byte("planted-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, stderr bytes.Buffer
	if code := Run([]string{"scrub", "--deny-file", deny}, strings.NewReader("status planted-token"), &out, &stderr); code != 0 || stderr.Len() != 0 || out.String() != "status [REDACTED]" {
		t.Fatal("CLI scrub failed")
	}
	for _, mode := range []os.FileMode{0o644, 0o640} {
		if err := os.Chmod(deny, mode); err != nil {
			t.Fatal(err)
		}
		assertRunPrivateFailure(t, deny, strings.NewReader("private-input-canary"))
	}
	if err := os.Chmod(deny, 0o600); err != nil {
		t.Fatal(err)
	}
	assertRunPrivateFailure(t, deny, errorReader{})
	assertRunPrivateFailure(t, root, strings.NewReader("private-input-canary"))
	assertRunPrivateFailure(t, filepath.Join(root, "missing-private-path-canary"), strings.NewReader("private-input-canary"))
	link := filepath.Join(root, "link-private-path-canary")
	if err := os.Symlink(deny, link); err != nil {
		t.Fatal(err)
	}
	assertRunPrivateFailure(t, link, strings.NewReader("private-input-canary"))
}

func assertRunPrivateFailure(t *testing.T, deny string, input io.Reader) {
	t.Helper()
	var out, stderr bytes.Buffer
	if code := Run([]string{"scrub", "--deny-file", deny}, input, &out, &stderr); code != 1 || out.Len() != 0 || stderr.Len() == 0 || strings.Contains(stderr.String(), "canary") {
		t.Fatal("failure was not private")
	}
}

func TestRunNewFromSubdirectory(t *testing.T) {
	root := testRepo(t)
	child := filepath.Join(root, "child")
	if err := os.Mkdir(child, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Chdir(child)
	var out, stderr bytes.Buffer
	if code := Run([]string{"new", "P0", "cli-test"}, strings.NewReader("private-input-canary"), &out, &stderr); code != 0 || stderr.Len() != 0 {
		t.Fatal("CLI new failed")
	}
	dir := strings.TrimSpace(out.String())
	if !strings.HasPrefix(dir, "evidence/p0/") {
		t.Fatal("unexpected CLI path")
	}
	if _, err := os.Stat(filepath.Join(root, dir, "run.json")); err != nil {
		t.Fatal("scaffold not under worktree root")
	}
}

func TestRunNewFailuresDoNotDiscloseInput(t *testing.T) {
	root := testRepo(t)
	t.Chdir(root)
	var out, stderr bytes.Buffer
	if code := Run([]string{"new", "P0", "../private-path-canary"}, errorReader{}, &out, &stderr); code != 1 || out.Len() != 0 || strings.Contains(stderr.String(), "canary") {
		t.Fatal("new failure disclosed input")
	}
	stderr.Reset()
	if code := Run([]string{"new", "P0", "valid"}, errorReader{}, errorWriter{}, &stderr); code != 1 || strings.Contains(stderr.String(), "canary") {
		t.Fatal("new output failure disclosed underlying error")
	}
	t.Chdir(t.TempDir())
	stderr.Reset()
	if code := Run([]string{"new", "P0", "test"}, errorReader{}, &out, &stderr); code != 1 || strings.Contains(stderr.String(), "canary") {
		t.Fatal("non-repository command did not fail safely")
	}
}

//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestBoundedQuery(t *testing.T) {
	for _, tc := range []struct {
		script  string
		pass    bool
		minimum time.Duration
	}{
		{"printf '0 16 verity V\\n'", true, 0},
		{"printf 'private detail' >&2", false, 0},
		{"printf '0 16 verity V\\n'; exit 1", false, 0},
		{"while :; do printf 'overflow'; done", false, 0},
		{"/bin/sleep 10", false, 250 * time.Millisecond},
		{"/bin/sleep 10 >/dev/null 2>&1 &", false, 0},
	} {
		t.Run(tc.script, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "query")
			if err := os.WriteFile(path, []byte("#!/bin/sh\n"+tc.script+"\n"), 0700); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
			defer cancel()
			started := time.Now()
			out, err := execute(ctx, path)
			elapsed := time.Since(started)
			if (err == nil) != tc.pass || elapsed < tc.minimum || elapsed > 2*time.Second {
				t.Fatal("unexpected query result or duration", err)
			}
			if tc.pass && string(out) != "0 16 verity V\n" || !tc.pass && out != nil {
				t.Fatal("failure exposed output or success changed bytes")
			}
		})
	}
}

func TestBoundedReads(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "input")
	if err := os.WriteFile(path, []byte("fixed"), 0600); err != nil {
		t.Fatal(err)
	}
	if raw, err := readBounded(path, 5); err != nil || string(raw) != "fixed" {
		t.Fatal(err)
	}
	if _, err := readBounded(path, 4); err == nil {
		t.Fatal("oversize read accepted")
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{link, root, filepath.Join(root, "missing")} {
		if _, err := readBounded(name, 10); err == nil {
			t.Fatal("nonregular or missing input accepted")
		}
	}
	var out boundedOutput
	if _, err := out.Write([]byte(strings.Repeat("x", 4096))); err != nil {
		t.Fatal(err)
	}
	if _, err := out.Write([]byte("x")); err == nil || out.Len() != 4096 {
		t.Fatal("stream cap exceeded")
	}
}

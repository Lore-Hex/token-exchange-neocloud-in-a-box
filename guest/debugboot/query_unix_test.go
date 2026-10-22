//go:build linux || darwin

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestBoundedQueryFiniteOutput(t *testing.T) {
	for _, size := range []int{4095, 4096, 4097, 65537} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "query")
			script := fmt.Sprintf("#!/bin/sh\nprintf '%%%ds' x\n", size)
			if err := os.WriteFile(path, []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			out, err := execute(context.Background(), path)
			if size <= 4096 {
				if err != nil || string(out) != strings.Repeat(" ", size-1)+"x" {
					t.Fatal("bounded stdout changed or was rejected")
				}
			} else if err == nil || out != nil {
				t.Fatal("oversize stdout accepted or exposed")
			}
		})
	}
}

func TestBoundedOutputCopy(t *testing.T) {
	for _, size := range []int{4095, 4096, 4097, 65537} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			var out boundedOutput
			if _, bypass := any(&out).(io.ReaderFrom); bypass {
				t.Fatal("stream cap must not expose ReaderFrom")
			}
			// Hide WriterTo to exercise io.Copy's destination ReaderFrom path,
			// as os/exec's pipe copier does for child stdout and stderr.
			source := struct{ io.Reader }{strings.NewReader(strings.Repeat("x", size))}
			n, err := io.Copy(&out, source)
			if size <= 4096 {
				if err != nil || n != int64(size) || out.buffer.Len() != size {
					t.Fatal("bounded copy changed bytes")
				}
			} else if err == nil || n > 4096 || out.buffer.Len() > 4096 {
				t.Fatal("copy bypassed stream cap")
			}
		})
	}
}

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
			// Only the timeout fixture shortens the production two-second cap.
			// Process startup under concurrent race suites can exceed 300 ms.
			deadline := 3 * time.Second
			if tc.minimum > 0 {
				deadline = 300 * time.Millisecond
			}
			ctx, cancel := context.WithTimeout(context.Background(), deadline)
			defer cancel()
			started := time.Now()
			out, err := execute(ctx, path)
			elapsed := time.Since(started)
			if (err == nil) != tc.pass || elapsed < tc.minimum || elapsed > 3*time.Second {
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
	if _, err := out.Write([]byte("x")); err == nil || out.buffer.Len() != 4096 {
		t.Fatal("stream cap exceeded")
	}
}

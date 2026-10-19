//go:build unix

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"bytes"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestPolicyFilesRejectSpecialFilesWithoutBlocking(t *testing.T) {
	dir := t.TempDir()
	fifo := filepath.Join(dir, "policy-fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{fifo, dir, "/dev/null"} {
		started := time.Now()
		if _, err := readPolicyFile(path); err == nil {
			t.Fatal("special policy file accepted")
		}
		if time.Since(started) > time.Second {
			t.Fatal("special policy file blocked")
		}
	}
	regular := filepath.Join(dir, "regular")
	if err := os.WriteFile(regular, []byte("regular"), 0600); err != nil {
		t.Fatal(err)
	}
	b, err := readPolicyFile(regular)
	if err != nil || !bytes.Equal(b, []byte("regular")) {
		t.Fatalf("regular file rejected: %v", err)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(regular, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readPolicyFile(link); err == nil {
		t.Fatal("policy symlink accepted")
	}
}

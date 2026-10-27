// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/elfmeta"
)

type failedReader struct{}

func (failedReader) Read([]byte) (int, error) { return 0, errors.New("untrusted detail") }

func TestArgumentsReadAndSizeReject(t *testing.T) {
	if out, err := inspect(failedReader{}, []string{"--execute"}); err != elfmeta.ErrRejected || len(out) != 0 {
		t.Fatal("accepted arguments")
	}
	if out, err := inspect(failedReader{}, nil); err != elfmeta.ErrRejected || len(out) != 0 {
		t.Fatal("accepted read failure")
	}
	r := bytes.NewReader(make([]byte, elfmeta.MaxBytes+100))
	if out, err := inspect(r, nil); err != elfmeta.ErrRejected || len(out) != 0 || r.Len() != 99 {
		t.Fatal("input limit failed")
	}
}

func TestBlockedInputDeadline(t *testing.T) {
	if os.Getenv("NIB_METADATA_DEADLINE_TEST") == "1" {
		os.Args = []string{"metadata"}
		main()
		return
	}
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	child := exec.Command(os.Args[0], "-test.run=^TestBlockedInputDeadline$")
	child.Env = append(os.Environ(), "NIB_METADATA_DEADLINE_TEST=1")
	child.Stdin = reader
	var output bytes.Buffer
	child.Stdout, child.Stderr = &output, &output
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	select {
	case err := <-done:
		if err == nil || child.ProcessState.ExitCode() != 1 || output.Len() != 0 {
			t.Fatal("blocked input did not fail closed")
		}
	case <-time.After(deadline + 5*time.Second):
		_ = child.Process.Kill()
		<-done
		t.Fatal("deadline absent")
	}
}

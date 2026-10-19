// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
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

func TestUnsupportedDoesNotEchoArguments(t *testing.T) {
	var out, errOut bytes.Buffer
	if code := run([]string{"not-a-command", "private-value"}, &out, &errOut); code != 2 {
		t.Fatalf("got %d", code)
	}
	if strings.Contains(out.String()+errOut.String(), "private-value") {
		t.Fatal("echoed a command argument")
	}
}

// SPDX-License-Identifier: Apache-2.0
package evidence

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"
	"testing/iotest"
)

func TestScrubLiteralPlantedValues(t *testing.T) {
	for _, tc := range []struct{ name, deny, input, want string }{
		{"token", "planted-token\n", "before planted-token after planted-token", "before [REDACTED] after [REDACTED]"},
		{"key-and-ips", "key.[test]+$\n192.0.2.15\n2001:db8::7\n", "key.[test]+$ 192.0.2.15 2001:db8::7", "[REDACTED] [REDACTED] [REDACTED]"},
		{"literal-not-regex", ".*\n", "abc .* xyz", "abc [REDACTED] xyz"},
		{"crlf-duplicates-blank", "\r\ncanary\r\ncanary\r\n", "canary\r\n", "[REDACTED]\r\n"},
		{"hash-not-comment", "#literal\n", "#literal literal", "[REDACTED] literal"},
		{"whitespace-significant", " value \n", "x value y value", "x[REDACTED]y value"},
		{"case-sensitive", "test\n", "TEST test", "TEST [REDACTED]"},
		{"overlap", "abc\nbcd\n", "abcd", "[REDACTED]"},
		{"self-overlap", "aaa\n", "aaaaa", "[REDACTED]"},
		{"adjacent", "abc\ndef\n", "abcdef", "[REDACTED]"},
		{"longer-first", "abcde\nabc\n", "abcde", "[REDACTED]"},
		{"shorter-first", "abc\nabcde\n", "abcde", "[REDACTED]"},
		{"key-lines", "key-line-one\nkey-line-two\n", "key-line-one\nkey-line-two\n", "[REDACTED]\n[REDACTED]\n"},
		{"binary-input", "secret\n", "\x00secret\xff", "\x00[REDACTED]\xff"},
		{"empty-input", "test\n", "", ""},
		{"no-match", "canary\n", "ordinary operational status\n", "ordinary operational status\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out bytes.Buffer
			if err := Scrub(strings.NewReader(tc.deny), iotest.OneByteReader(strings.NewReader(tc.input)), &out); err != nil {
				t.Fatal(err)
			}
			if out.String() != tc.want {
				t.Fatal("redaction mismatch (input and output withheld)")
			}
		})
	}
}

type errorReader struct{}

func (errorReader) Read(p []byte) (int, error) {
	return copy(p, "private-input-canary"), errors.New("private-error-canary")
}

type errorWriter struct{ short bool }

func (w errorWriter) Write([]byte) (int, error) {
	if w.short {
		return 0, nil
	}
	return 0, errors.New("private-error-canary")
}

func TestScrubReadFailuresAreAtomicAndPrivate(t *testing.T) {
	for _, tc := range []struct {
		name        string
		deny, input io.Reader
	}{
		{"deny-error", errorReader{}, strings.NewReader("private-input-canary")},
		{"input-error", strings.NewReader("deny"), errorReader{}},
		{"empty-deny", strings.NewReader("\n\r\n"), strings.NewReader("private-input-canary")},
		{"nul-deny", strings.NewReader("deny\x00canary"), strings.NewReader("private-input-canary")},
		{"cr-deny", strings.NewReader("deny\rcanary"), strings.NewReader("private-input-canary")},
		{"long-entry", strings.NewReader(strings.Repeat("x", maxLiteralLen+1)), strings.NewReader("private-input-canary")},
		{"large-deny", strings.NewReader(strings.Repeat("x", maxDenyBytes+1)), strings.NewReader("private-input-canary")},
		{"marker-collision", strings.NewReader("REDACTED"), strings.NewReader("REDACTED private-input-canary")},
		{"boundary-collision", strings.NewReader("abc\nx[REDACTED]y"), strings.NewReader("xabcy")},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out bytes.Buffer
			err := Scrub(tc.deny, tc.input, &out)
			if err == nil || out.Len() != 0 {
				t.Fatal("invalid input did not fail without output")
			}
			if strings.Contains(err.Error(), "canary") {
				t.Fatal("error disclosed input")
			}
		})
	}
}

func TestScrubResourceLimits(t *testing.T) {
	var deny strings.Builder
	for i := range maxLiterals + 1 {
		fmt.Fprintf(&deny, "literal-%d\n", i)
	}
	var out bytes.Buffer
	if err := Scrub(strings.NewReader(deny.String()), strings.NewReader("anything"), &out); err == nil || out.Len() != 0 {
		t.Fatal("too many literals accepted")
	}
	if err := Scrub(strings.NewReader("deny"), io.LimitReader(zeroReader{}, maxInputBytes+1), &out); err == nil || out.Len() != 0 {
		t.Fatal("oversized input accepted")
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) { clear(p); return len(p), nil }

func TestScrubWriterErrorsArePrivate(t *testing.T) {
	for _, writer := range []io.Writer{errorWriter{}, errorWriter{short: true}} {
		err := Scrub(strings.NewReader("deny"), strings.NewReader("status"), writer)
		if err == nil || strings.Contains(err.Error(), "canary") {
			t.Fatal("writer failure not safely reported")
		}
	}
}

func FuzzScrubNeverEmitsDeniedLiteral(f *testing.F) {
	f.Add("abc", "xxabcabcxx")
	f.Add("REDACTED", "REDACTED")
	f.Add("aaa", "aaaaa")
	f.Fuzz(func(t *testing.T, literal, input string) {
		if len(literal) == 0 || len(literal) > 64 || len(input) > 4096 || strings.ContainsAny(literal, "\x00\r\n") {
			return
		}
		var out bytes.Buffer
		err := Scrub(strings.NewReader(literal), strings.NewReader(input), &out)
		if err != nil {
			if out.Len() != 0 {
				t.Fatal("failure emitted content")
			}
			return
		}
		if bytes.Contains(out.Bytes(), []byte(literal)) {
			t.Fatal("literal survived redaction")
		}
	})
}

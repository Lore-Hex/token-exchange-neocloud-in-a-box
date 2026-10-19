// SPDX-License-Identifier: Apache-2.0
package evidence

import (
	"bytes"
	"errors"
	"io"
)

const (
	maxDenyBytes  = 1 << 20
	maxInputBytes = 64 << 20
	maxLiterals   = 1024
	maxLiteralLen = 64 << 10
	redacted      = "[REDACTED]"
)

// Scrub replaces the union of all literal deny-list matches, including
// overlapping matches. Entries are LF/CRLF-separated, case-sensitive bytes;
// blank lines are ignored, but spaces and '#' are literal (not comments).
// Limits: 1 MiB deny file, 1024 unique entries, 64 KiB per entry, 64 MiB input.
// Nothing is written before both inputs have been read and sanitized. Errors
// never include input bytes or underlying reader/writer errors. This is NOT a
// detector for inference contents, unlisted secrets or encoded credentials.
func Scrub(deny, input io.Reader, output io.Writer) error {
	literals, err := readLiterals(deny)
	if err != nil {
		return err
	}
	data, err := readBounded(input, maxInputBytes)
	if err != nil {
		return errors.New("evidence: cannot read scrub input or input exceeds limit")
	}
	masked := make([]bool, len(data))
	for _, literal := range literals {
		// Merge overlapping matches before marking, avoiding repeated writes
		// for self-overlapping literals such as a long string of one byte.
		start, end := -1, -1
		mark := func() {
			for i := start; i < end; i++ {
				masked[i] = true
			}
		}
		for offset := 0; offset <= len(data)-len(literal); {
			i := bytes.Index(data[offset:], literal)
			if i < 0 {
				break
			}
			i += offset
			if start < 0 {
				start, end = i, i+len(literal)
			} else if i <= end {
				end = i + len(literal)
			} else {
				mark()
				start, end = i, i+len(literal)
			}
			offset = i + 1
		}
		if start >= 0 {
			mark()
		}
	}
	var clean bytes.Buffer
	for i := 0; i < len(data); {
		if !masked[i] {
			clean.WriteByte(data[i])
			i++
			continue
		}
		clean.WriteString(redacted)
		for i < len(data) && masked[i] {
			i++
		}
	}
	// A marker or a newly joined boundary must not reintroduce a denied value.
	for _, literal := range literals {
		if bytes.Contains(clean.Bytes(), literal) {
			return errors.New("evidence: redaction marker conflicts with deny list")
		}
	}
	n, err := output.Write(clean.Bytes())
	if err != nil || n != clean.Len() {
		return errors.New("evidence: cannot write scrubbed output")
	}
	return nil
}

func readLiterals(input io.Reader) ([][]byte, error) {
	data, err := readBounded(input, maxDenyBytes)
	if err != nil {
		return nil, errors.New("evidence: cannot read deny file or deny file exceeds limit")
	}
	seen := make(map[string]bool)
	var literals [][]byte
	for _, line := range bytes.Split(data, []byte{'\n'}) {
		line = bytes.TrimSuffix(line, []byte{'\r'})
		if len(line) == 0 {
			continue
		}
		if len(line) > maxLiteralLen || bytes.ContainsAny(line, "\x00\r") {
			return nil, errors.New("evidence: invalid deny-file entry")
		}
		if !seen[string(line)] {
			seen[string(line)] = true
			literals = append(literals, line)
		}
		if len(literals) > maxLiterals {
			return nil, errors.New("evidence: too many deny-file entries")
		}
	}
	if len(literals) == 0 {
		return nil, errors.New("evidence: deny file must contain at least one literal")
	}
	return literals, nil
}

func readBounded(input io.Reader, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errors.New("evidence: bounded read failed")
	}
	return data, nil
}

// SPDX-License-Identifier: Apache-2.0

package config

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// MaxManifestBytes bounds files.sha256 metadata, not the downloaded weights.
const MaxManifestBytes = 32 << 20

// ManifestFile is one validated file in a complete model pack.
type ManifestFile struct {
	SHA256 string
	Bytes  int64
	Path   string
}

// ValidateFileManifest validates PLAN section 7's sorted
// "<sha256>  <bytes>  <path>" format and checks the exact byte total. It does not
// download weights or claim that a file's content matches its declared hash.
func ValidateFileManifest(data []byte, totalBytes int64) error {
	_, err := ParseFileManifest(data, totalBytes)
	return err
}

// ParseFileManifest shares the validator's strict syntax and total accounting.
// No partial entries are returned on invalid input.
func ParseFileManifest(data []byte, totalBytes int64) ([]ManifestFile, error) {
	if len(data) > MaxManifestBytes {
		return nil, errors.New("manifest exceeds size limit")
	}
	all, err := compileSchemas()
	if err != nil {
		return nil, err
	}
	var files []ManifestFile
	var total int64
	previous := ""
	line := 0
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line++
		parts := strings.Split(scanner.Text(), "  ")
		if len(parts) != 3 {
			return nil, fmt.Errorf("manifest line %d: expected sha256, bytes and path separated by two spaces", line)
		}
		size, err := strconv.ParseInt(parts[1], 10, 64)
		if err != nil || strconv.FormatInt(size, 10) != parts[1] {
			return nil, fmt.Errorf("manifest line %d: invalid decimal byte count", line)
		}
		entry := map[string]any{"sha256": parts[0], "bytes": size, "path": parts[2]}
		if err := validateValue(all["common"], entry); err != nil {
			return nil, fmt.Errorf("manifest line %d: %w", line, err)
		}
		if parts[2] <= previous {
			return nil, fmt.Errorf("manifest line %d: paths must be unique and strictly sorted", line)
		}
		previous = parts[2]
		if total > (1<<63-1)-size {
			return nil, errors.New("manifest byte total overflows int64")
		}
		total += size
		files = append(files, ManifestFile{SHA256: parts[0], Bytes: size, Path: parts[2]})
	}
	if err := scanner.Err(); err != nil {
		return nil, errors.New("manifest contains an oversized or unreadable line")
	}
	if line == 0 {
		return nil, errors.New("manifest must contain at least one file")
	}
	if total != totalBytes {
		return nil, errors.New("manifest byte total differs from total_bytes")
	}
	return files, nil
}

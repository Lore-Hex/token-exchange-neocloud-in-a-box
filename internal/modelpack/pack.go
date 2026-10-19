// SPDX-License-Identifier: Apache-2.0

// Package modelpack verifies pinned public model metadata without downloading
// weight objects. It is operator tooling, not guest or attestation code.
package modelpack

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"go.yaml.in/yaml/v3"
)

// Pack contains the integrity fields used by this package. The full document is
// validated using config.ValidateModelPack before these fields are decoded.
type Pack struct {
	Repository     string `yaml:"hf_repo"`
	Revision       string `yaml:"hf_revision"`
	TotalBytes     int64  `yaml:"total_bytes"`
	EngineProfiles map[string]struct {
		Image string `yaml:"image"`
	} `yaml:"engine_profiles"`
}

type File struct {
	Path   string `json:"path"`
	Bytes  int64  `json:"bytes"`
	SHA256 string `json:"sha256"`
}

// Manifest contains every file at the pinned commit, including licenses and
// non-weight files. A matching remote manifest does not prove weight contents.
type Manifest struct {
	Repository      string `json:"hf_repo"`
	Revision        string `json:"hf_revision"`
	Files           []File `json:"files"`
	TotalBytes      int64  `json:"total_bytes"`
	DownloadedBytes int64  `json:"downloaded_bytes"`
}

func (m Manifest) MarshalText() ([]byte, error) {
	files := append([]File(nil), m.Files...)
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	var b bytes.Buffer
	for _, f := range files {
		fmt.Fprintf(&b, "%s  %d  %s\n", f.SHA256, f.Bytes, f.Path)
	}
	if err := config.ValidateFileManifest(b.Bytes(), m.TotalBytes); err != nil {
		return nil, errors.New("modelpack: invalid generated manifest")
	}
	return b.Bytes(), nil
}

// Generate hashes bounded non-LFS files and takes weight hashes from lfs.oid.
// It requires an immutable revision and never writes pack files or downloads LFS.
func Generate(ctx context.Context, repository, revision string) (*Manifest, error) {
	return newRemote().generate(ctx, repository, revision)
}

// Load accepts a pack.yaml path or its containing directory. Diagnostics never
// include caller-controlled filenames, YAML values, URLs or response bodies.
func Load(packPath string) (*Pack, []byte, error) {
	info, err := os.Lstat(packPath)
	if err != nil || info.Mode()&os.ModeSymlink != 0 {
		return nil, nil, errors.New("modelpack: cannot open pack")
	}
	if info.IsDir() {
		packPath = filepath.Join(packPath, "pack.yaml")
	}
	root, err := os.OpenRoot(filepath.Dir(packPath))
	if err != nil {
		return nil, nil, errors.New("modelpack: cannot open pack directory")
	}
	defer root.Close()
	b, err := readRegular(root, filepath.Base(packPath), config.MaxDocumentBytes)
	if err != nil {
		return nil, nil, err
	}
	if config.ValidateModelPack(b) != nil {
		return nil, nil, errors.New("modelpack: invalid pack schema")
	}
	var p Pack
	if yaml.Unmarshal(b, &p) != nil {
		return nil, nil, errors.New("modelpack: invalid pack integrity fields")
	}
	manifest, err := readRegular(root, "files.sha256", config.MaxManifestBytes)
	if err != nil {
		return nil, nil, err
	}
	if config.ValidateFileManifest(manifest, p.TotalBytes) != nil {
		return nil, nil, errors.New("modelpack: invalid local manifest or total")
	}
	return &p, manifest, nil
}

func readRegular(root *os.Root, name string, limit int64) ([]byte, error) {
	i, err := root.Lstat(name)
	if err != nil || !i.Mode().IsRegular() || i.Size() > limit {
		return nil, errors.New("modelpack: local file missing, unsafe or too large")
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, errors.New("modelpack: cannot read local file")
	}
	defer f.Close()
	i, err = f.Stat()
	if err != nil || !i.Mode().IsRegular() {
		return nil, errors.New("modelpack: local file is not regular")
	}
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(b)) > limit {
		return nil, errors.New("modelpack: local file read failed or exceeded limit")
	}
	return b, nil
}

// CheckRemote re-fetches every file's metadata at the pinned revision and hashes
// non-LFS files. It checks completeness and exact totals, not deployment safety
// or registry images; VerifyImage handles registry manifest integrity separately.
func CheckRemote(ctx context.Context, packPath string) error {
	return checkRemote(ctx, packPath, newRemote())
}

func checkRemote(ctx context.Context, packPath string, r *remote) error {
	p, expected, err := Load(packPath)
	if err != nil {
		return err
	}
	m, err := r.generate(ctx, p.Repository, p.Revision)
	if err != nil {
		return err
	}
	actual, err := m.MarshalText()
	if err != nil {
		return err
	}
	// Permit the final newline and CRLF choices accepted by the shared validator.
	normalize := func(b []byte) string { return strings.TrimSuffix(strings.ReplaceAll(string(b), "\r\n", "\n"), "\n") }
	if m.TotalBytes != p.TotalBytes || normalize(actual) != normalize(expected) {
		return errors.New("modelpack: remote file set, size or SHA-256 differs")
	}
	return nil
}

// Run implements the model subcommand with sanitized errors and output.
func Run(ctx context.Context, args []string, out, errOut io.Writer) int {
	const usage = "usage: nib model check-remote <pack.yaml-or-directory>"
	if len(args) == 1 && (args[0] == "help" || args[0] == "--help") {
		fmt.Fprintln(out, usage)
		return 0
	}
	if len(args) != 2 || args[0] != "check-remote" {
		fmt.Fprintln(errOut, usage)
		return 2
	}
	if err := CheckRemote(ctx, args[1]); err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	if _, err := fmt.Fprintln(out, "Remote model manifest matches; weights not downloaded; no deployment or hardware validation."); err != nil {
		return 1
	}
	return 0
}

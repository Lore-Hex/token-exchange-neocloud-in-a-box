// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sync"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

// LocalProof describes bytes read from a staged local directory, not a sealed
// snapshot, a trusted model, or permission to start an engine. A trusted pinned
// pack and a quiescent staging directory are prerequisites. dm-verity and measured
// boot must separately protect the eventual runtime against later modification.
type LocalProof struct {
	Repository     string `json:"hf_repo"`
	Revision       string `json:"hf_revision"`
	ManifestSHA256 string `json:"manifest_sha256"`
	Files          int    `json:"files"`
	VerifiedBytes  int64  `json:"verified_bytes"`
}

// CheckLocal reads every file exactly once using bounded memory and makes no
// network requests or writes. Extra files/directories, symlinks and special files
// are rejected; a Hugging Face cache must first be materialized outside its cache.
func CheckLocal(ctx context.Context, packPath, directory string) (*LocalProof, error) {
	return CheckLocalWithWorkers(ctx, packPath, directory, 1)
}

// MaxLocalWorkers bounds local hashing to 2 MiB of scratch buffers and eight
// concurrent file readers. It does not change which files or bytes are checked.
const MaxLocalWorkers = 8

// CheckLocalWithWorkers opts into bounded parallel hashing; CheckLocal remains serial.
func CheckLocalWithWorkers(ctx context.Context, packPath, directory string, workers int) (*LocalProof, error) {
	if workers < 1 || workers > MaxLocalWorkers {
		return nil, errors.New("modelpack: local verification workers out of range")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	// Reject lexical rewrites rather than allowing a trailing slash or dot to
	// make Lstat follow a root symlink. Callers provide one canonical spelling.
	if directory == "" || filepath.Clean(directory) != directory {
		return nil, errors.New("modelpack: canonical staged directory path required")
	}
	p, manifest, err := Load(packPath)
	if err != nil {
		return nil, err
	}
	files, err := config.ParseFileManifest(manifest, p.TotalBytes)
	if err != nil {
		return nil, errors.New("modelpack: invalid local manifest")
	}
	i, err := os.Lstat(directory)
	if err != nil || !i.IsDir() || i.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("modelpack: staged directory missing or unsafe")
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, errors.New("modelpack: cannot open staged directory")
	}
	defer root.Close()
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(i, opened) {
		return nil, errors.New("modelpack: staged directory changed")
	}
	if err := verifyLocalSetWorkers(ctx, root, files, workers); err != nil {
		return nil, err
	}
	return localProof(p, manifest, len(files)), nil
}

func localProof(p *Pack, manifest []byte, count int) *LocalProof {
	return &LocalProof{Repository: p.Repository, Revision: p.Revision, ManifestSHA256: fmt.Sprintf("%x", sha256.Sum256(manifest)), Files: count, VerifiedBytes: p.TotalBytes}
}

func localFileSet(files []config.ManifestFile) (map[string]config.ManifestFile, map[string]bool) {
	want, dirs := make(map[string]config.ManifestFile, len(files)), map[string]bool{".": true}
	for _, f := range files {
		want[f.Path] = f
		for d := path.Dir(f.Path); d != "."; d = path.Dir(d) {
			dirs[d] = true
		}
	}
	return want, dirs
}

func verifyLocalSet(ctx context.Context, root *os.Root, files []config.ManifestFile) error {
	return verifyLocalSetWorkers(ctx, root, files, 1)
}

func verifyLocalSetWorkers(ctx context.Context, root *os.Root, files []config.ManifestFile, workers int) error {
	want, dirs := localFileSet(files)
	initial, err := localInventory(ctx, root, want, dirs)
	if err != nil {
		return err
	}
	if err := localChecks(ctx, files, workers, func(ctx context.Context, f config.ManifestFile, buf []byte) error {
		return verifyLocalFile(ctx, root, f, initial[f.Path], buf)
	}); err != nil {
		return err
	}
	final, err := localInventory(ctx, root, want, dirs)
	if err != nil {
		return err
	}
	for name, before := range initial {
		if !sameLocalFile(before, final[name]) {
			return errors.New("modelpack: staged files changed during verification")
		}
	}
	return nil
}

func localChecks(ctx context.Context, files []config.ManifestFile, workers int, check func(context.Context, config.ManifestFile, []byte) error) error {
	if workers < 1 || workers > MaxLocalWorkers {
		return errors.New("modelpack: local verification workers out of range")
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make(chan config.ManifestFile)
	var wg sync.WaitGroup
	var once sync.Once
	var firstErr error
	for range min(workers, len(files)) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, 256<<10)
			for f := range jobs {
				if ctx.Err() != nil {
					return
				}
				if err := check(ctx, f, buf); err != nil {
					once.Do(func() { firstErr = err; cancel() })
					return
				}
			}
		}()
	}
send:
	for _, f := range files {
		select {
		case jobs <- f:
		case <-ctx.Done():
			break send
		}
	}
	close(jobs)
	// No return or final inventory until every reader has closed its file.
	wg.Wait()
	if firstErr != nil {
		return firstErr
	}
	return ctx.Err()
}

func localInventory(ctx context.Context, root *os.Root, want map[string]config.ManifestFile, dirs map[string]bool) (map[string]os.FileInfo, error) {
	return inventoryLocal(ctx, root, want, dirs, true)
}

func inventoryLocal(ctx context.Context, root *os.Root, want map[string]config.ManifestFile, dirs map[string]bool, complete bool) (map[string]os.FileInfo, error) {
	seen := make(map[string]os.FileInfo, len(want))
	seenDirs := map[string]bool{".": true}
	pending := []string{"."}
	for len(pending) != 0 {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		dir := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		f, err := openLocalWeight(root, dir)
		if err != nil {
			return nil, errors.New("modelpack: cannot safely open staged directory")
		}
		i, err := f.Stat()
		if err != nil || !i.IsDir() {
			f.Close()
			return nil, errors.New("modelpack: staged directory changed")
		}
		err = readLocalDirectory(ctx, f, func(entry fs.DirEntry) error {
			name := path.Join(dir, entry.Name())
			if entry.IsDir() && dirs[name] {
				if seenDirs[name] {
					return errors.New("modelpack: duplicate staged directory")
				}
				seenDirs[name] = true
				pending = append(pending, name)
				return nil
			}
			expected, ok := want[name]
			if !ok || !entry.Type().IsRegular() {
				return errors.New("modelpack: staged file set differs or contains unsafe entries")
			}
			i, err := entry.Info()
			if err != nil || !i.Mode().IsRegular() || i.Size() != expected.Bytes {
				return errors.New("modelpack: staged file size or type differs")
			}
			if _, duplicate := seen[name]; duplicate {
				return errors.New("modelpack: duplicate staged file")
			}
			seen[name] = i
			return nil
		})
		f.Close()
		if err != nil {
			return nil, err
		}
	}
	if complete && len(seen) != len(want) {
		return nil, errors.New("modelpack: staged files missing")
	}
	return seen, nil
}

const localDirectoryBatch = 64

type localDirectoryReader interface {
	ReadDir(int) ([]fs.DirEntry, error)
}

// Do not use WalkDir/ReadDir(-1): unexpected directory contents must not cause
// unbounded allocation or sorting before we can reject them or honor cancel.
func readLocalDirectory(ctx context.Context, dir localDirectoryReader, visit func(fs.DirEntry) error) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		entries, readErr := dir.ReadDir(localDirectoryBatch)
		if err := ctx.Err(); err != nil {
			return err
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return errors.New("modelpack: cannot inspect staged directory")
		}
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := visit(entry); err != nil {
				return err
			}
		}
		if errors.Is(readErr, io.EOF) {
			return nil
		}
		if len(entries) == 0 {
			return errors.New("modelpack: staged directory enumeration made no progress")
		}
	}
}

func sameLocalFile(a, b os.FileInfo) bool {
	return a != nil && b != nil && b.Mode().IsRegular() && os.SameFile(a, b) && a.Size() == b.Size() && a.Mode() == b.Mode() && a.ModTime().Equal(b.ModTime())
}

func verifyLocalFile(ctx context.Context, root *os.Root, expected config.ManifestFile, before os.FileInfo, buf []byte) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	f, err := openLocalWeight(root, expected.Path)
	if err != nil {
		return errors.New("modelpack: cannot safely open staged file")
	}
	defer f.Close()
	i, err := f.Stat()
	if err != nil || !sameLocalFile(before, i) {
		return errors.New("modelpack: staged file changed before hashing")
	}
	h := sha256.New()
	n, err := io.CopyBuffer(h, &localReader{ctx: ctx, r: io.LimitReader(f, expected.Bytes+1)}, buf)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.New("modelpack: staged file read failed")
	}
	if n != expected.Bytes || fmt.Sprintf("%x", h.Sum(nil)) != expected.SHA256 {
		return errors.New("modelpack: staged file content differs from pinned manifest")
	}
	i, err = f.Stat()
	if err != nil || !sameLocalFile(before, i) {
		return errors.New("modelpack: staged file changed while hashing")
	}
	return ctx.Err()
}

type localReader struct {
	ctx context.Context
	r   io.Reader
}

func (r *localReader) Read(b []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.r.Read(b)
}

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func localFixture(t *testing.T) (string, string) {
	t.Helper()
	m := &Manifest{Repository: "org/model", Revision: testRev}
	dir := t.TempDir()
	for _, name := range []string{"config.json", "sub/empty", "sub/weight.safetensors"} {
		body := []byte("fixture content " + name)
		if name == "sub/empty" {
			body = nil
		}
		m.Files = append(m.Files, File{Path: name, Bytes: int64(len(body)), SHA256: fmt.Sprintf("%x", sha256.Sum256(body))})
		m.TotalBytes += int64(len(body))
		if err := os.MkdirAll(filepath.Dir(filepath.Join(dir, name)), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return writePack(t, m), dir
}

func TestCheckLocalComplete(t *testing.T) {
	p, dir := localFixture(t)
	proof, err := CheckLocal(context.Background(), p, dir)
	if err != nil {
		t.Fatal(err)
	}
	pack, manifest, err := Load(p)
	if err != nil || proof.Files != 3 || proof.VerifiedBytes != pack.TotalBytes || proof.Repository != pack.Repository || proof.Revision != pack.Revision || proof.ManifestSHA256 != fmt.Sprintf("%x", sha256.Sum256(manifest)) {
		t.Fatal("invalid local proof")
	}
	var out, errs bytes.Buffer
	if Run(context.Background(), []string{"check-local", p, dir}, &out, &errs) != 0 || errs.Len() != 0 || !strings.Contains(out.String(), "no immutable snapshot") {
		t.Fatalf("CLI result: %s %s", out.String(), errs.String())
	}
}

func TestCheckLocalRejectsMutations(t *testing.T) {
	mutations := map[string]func(string) error{
		"missing":   func(d string) error { return os.Remove(filepath.Join(d, "config.json")) },
		"truncated": func(d string) error { return os.Truncate(filepath.Join(d, "config.json"), 1) },
		"same-size-content": func(d string) error {
			file := filepath.Join(d, "config.json")
			b, err := os.ReadFile(file)
			if err != nil {
				return err
			}
			b[0] ^= 1
			return os.WriteFile(file, b, 0600)
		},
		"extra-file":      func(d string) error { return os.WriteFile(filepath.Join(d, "SECRET"), []byte("PRIVATE"), 0600) },
		"extra-directory": func(d string) error { return os.Mkdir(filepath.Join(d, "SECRET"), 0700) },
		"file-to-directory": func(d string) error {
			file := filepath.Join(d, "config.json")
			if err := os.Remove(file); err != nil {
				return err
			}
			return os.Mkdir(file, 0700)
		},
		"internal-link": func(d string) error {
			file := filepath.Join(d, "config.json")
			if err := os.Rename(file, filepath.Join(d, "hidden")); err != nil {
				return err
			}
			return os.Symlink("hidden", file)
		},
		"external-link": func(d string) error {
			file := filepath.Join(d, "config.json")
			if err := os.Remove(file); err != nil {
				return err
			}
			return os.Symlink("/missing/SECRET", file)
		},
		"directory-link": func(d string) error {
			if err := os.Rename(filepath.Join(d, "sub"), filepath.Join(d, "other")); err != nil {
				return err
			}
			return os.Symlink("other", filepath.Join(d, "sub"))
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			p, dir := localFixture(t)
			if err := mutate(dir); err != nil {
				t.Fatal(err)
			}
			for _, workers := range []int{1, 2, MaxLocalWorkers} {
				proof, err := CheckLocalWithWorkers(context.Background(), p, dir, workers)
				if proof != nil || err == nil || strings.Contains(err.Error(), "SECRET") || strings.Contains(err.Error(), "PRIVATE") || strings.Contains(err.Error(), dir) {
					t.Fatalf("mutation accepted or leaked with %d workers: %v", workers, err)
				}
			}
		})
	}
}

func TestCheckLocalCancellationAndUnsafeRoot(t *testing.T) {
	p, dir := localFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if proof, err := CheckLocal(ctx, p, dir); proof != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("ignored cancellation")
	}
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(dir, link); err != nil {
		t.Fatal(err)
	}
	for _, root := range []string{link, link + "/", link + "/.", dir + "/../" + filepath.Base(dir), filepath.Join(dir, "config.json"), "/missing/SECRET"} {
		if proof, err := CheckLocal(context.Background(), p, root); proof != nil || err == nil || strings.Contains(err.Error(), "SECRET") {
			t.Fatal("unsafe root accepted or leaked")
		}
	}
}

type directoryReaderFunc func(int) ([]fs.DirEntry, error)

func (f directoryReaderFunc) ReadDir(n int) ([]fs.DirEntry, error) { return f(n) }

func TestLocalDirectoryEnumerationBounded(t *testing.T) {
	i, err := os.Stat(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	entry := fs.FileInfoToDirEntry(i)
	calls := 0
	dir := directoryReaderFunc(func(n int) ([]fs.DirEntry, error) {
		calls++
		if n != localDirectoryBatch {
			t.Fatal("unbounded directory read")
		}
		entries := make([]fs.DirEntry, n)
		for i := range entries {
			entries[i] = entry
		}
		return entries, nil
	})
	rejected := errors.New("unexpected entry")
	if err := readLocalDirectory(context.Background(), dir, func(fs.DirEntry) error { return rejected }); !errors.Is(err, rejected) || calls != 1 {
		t.Fatal("enumerated beyond first unexpected batch")
	}
	ctx, cancel := context.WithCancel(context.Background())
	calls = 0
	visited := 0
	if err := readLocalDirectory(ctx, dir, func(fs.DirEntry) error {
		visited++
		if visited == localDirectoryBatch+1 {
			cancel()
		}
		return nil
	}); !errors.Is(err, context.Canceled) || calls != 2 || visited != localDirectoryBatch+1 {
		t.Fatal("ignored mid-enumeration cancellation")
	}
}

func TestLocalDirectoryReadErrorsAndCancellation(t *testing.T) {
	for _, kind := range []string{"cancel", "read-error", "no-progress", "end"} {
		t.Run(kind, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			dir := directoryReaderFunc(func(n int) ([]fs.DirEntry, error) {
				if n != localDirectoryBatch {
					t.Fatal("unbounded read")
				}
				switch kind {
				case "cancel":
					cancel()
					return nil, io.EOF
				case "read-error":
					return nil, errors.New("SECRET")
				case "no-progress":
					return nil, nil
				default:
					return nil, io.EOF
				}
			})
			err := readLocalDirectory(ctx, dir, func(fs.DirEntry) error { t.Fatal("unexpected entry"); return nil })
			if (err == nil) != (kind == "end") || (err != nil && strings.Contains(err.Error(), "SECRET")) {
				t.Fatal("invalid result or leaked diagnostic")
			}
			if kind == "cancel" && !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation lost")
			}
		})
	}
}

func TestCheckLocalDetectsFileReplacement(t *testing.T) {
	p, dir := localFixture(t)
	pack, manifest, err := Load(p)
	if err != nil {
		t.Fatal(err)
	}
	files, err := config.ParseFileManifest(manifest, pack.TotalBytes)
	if err != nil {
		t.Fatal(err)
	}
	r, err := os.OpenRoot(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	f := files[0]
	before, err := r.Stat(f.Path)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, f.Path)
	body, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(file, file+".old"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, body, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(file, before.ModTime(), before.ModTime()); err != nil {
		t.Fatal(err)
	}
	if verifyLocalFile(context.Background(), r, f, before, make([]byte, 16)) == nil {
		t.Fatal("same-content inode replacement accepted")
	}
}

type cancelingReader struct{ cancel context.CancelFunc }

func (r cancelingReader) Read(b []byte) (int, error) { b[0] = 'x'; r.cancel(); return 1, nil }

func TestLocalReaderChecksCancellationBetweenChunks(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	n, err := io.CopyBuffer(io.Discard, &localReader{ctx: ctx, r: cancelingReader{cancel}}, make([]byte, 4))
	if n != 1 || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation ignored")
	}
}

func TestCheckLocalCLIRejectsBadInput(t *testing.T) {
	for _, args := range [][]string{{"check-local"}, {"check-local", "SECRET"}, {"check-local", "SECRET", "SECRET"}, {"check-local", "a", "b", "c"}} {
		var out, errs bytes.Buffer
		if Run(context.Background(), args, &out, &errs) == 0 || strings.Contains(out.String()+errs.String(), "SECRET") {
			t.Fatal("CLI accepted or leaked")
		}
	}
}

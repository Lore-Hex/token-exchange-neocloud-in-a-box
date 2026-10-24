// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

// StagePlan describes pinned public bytes. Planning does not access the network,
// create directories, reserve storage, or approve an infrastructure expense.
type StagePlan struct {
	Repository         string `json:"hf_repo"`
	Revision           string `json:"hf_revision"`
	ManifestSHA256     string `json:"manifest_sha256"`
	Files              int    `json:"files"`
	TotalBytes         int64  `json:"total_bytes"`
	MinimumBudgetBytes int64  `json:"minimum_budget_bytes"`
}

type stageInput struct {
	pack     *Pack
	manifest []byte
	files    []config.ManifestFile
	plan     StagePlan
}

func loadStage(packPath string) (*stageInput, error) {
	p, manifest, err := Load(packPath)
	if err != nil {
		return nil, err
	}
	files, err := config.ParseFileManifest(manifest, p.TotalBytes)
	if err != nil {
		return nil, errors.New("modelpack: invalid staging manifest")
	}
	return &stageInput{pack: p, manifest: manifest, files: files, plan: StagePlan{
		Repository: p.Repository, Revision: p.Revision, ManifestSHA256: fmt.Sprintf("%x", sha256.Sum256(manifest)),
		Files: len(files), TotalBytes: p.TotalBytes, MinimumBudgetBytes: p.TotalBytes + int64(len(files)),
	}}, nil
}

func PlanStage(packPath string) (*StagePlan, error) {
	in, err := loadStage(packPath)
	if err != nil {
		return nil, err
	}
	return &in.plan, nil
}

type StageOptions struct {
	MaxBytes int64
	Timeout  time.Duration
	Workers  int
}

// MaxStageWorkers bounds concurrent public downloads, not the total byte budget.
const MaxStageWorkers = 8

type StageProof struct {
	*LocalProof
	DownloadedBytes int64 `json:"downloaded_bytes"`
	ReusedFiles     int   `json:"reused_files"`
}

// Stage downloads public pinned files only. Existing files are hashed before
// reuse; mismatches and unexpected entries are never overwritten or deleted.
// The directory must be private, quiescent and on a local POSIX filesystem.
// This is preparation, not an immutable snapshot or permission to serve.
func Stage(ctx context.Context, packPath, directory string, options StageOptions) (*StageProof, error) {
	d := newWeightDownload()
	defer d.client.CloseIdleConnections()
	return stage(ctx, packPath, directory, options, d)
}

func stage(ctx context.Context, packPath, directory string, options StageOptions, download *weightDownload) (*StageProof, error) {
	return stageWithSync(ctx, packPath, directory, options, download, syncStageDirectory)
}

func stageWithSync(ctx context.Context, packPath, directory string, options StageOptions, download *weightDownload, syncDirectory func(*os.Root, string) error) (*StageProof, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	in, err := loadStage(packPath)
	if err != nil {
		return nil, err
	}
	if options.MaxBytes < in.plan.MinimumBudgetBytes || options.Timeout <= 0 || options.Timeout > 24*time.Hour {
		return nil, errors.New("modelpack: explicit sufficient byte budget and timeout up to 24h required")
	}
	workers := options.Workers
	if workers == 0 {
		workers = 1
	}
	if workers < 1 || workers > MaxStageWorkers {
		return nil, fmt.Errorf("modelpack: download workers must be between 1 and %d", MaxStageWorkers)
	}
	if !filepath.IsAbs(directory) || filepath.Clean(directory) != directory {
		return nil, errors.New("modelpack: canonical absolute staging directory required")
	}
	ctx, cancel := context.WithTimeout(ctx, options.Timeout)
	defer cancel()
	parent, err := os.OpenRoot(filepath.Dir(directory))
	if err != nil {
		return nil, errors.New("modelpack: cannot open staging parent directory")
	}
	defer parent.Close()
	name := filepath.Base(directory)
	// Mkdir does not replace any existing entry or create unreviewed parents.
	if err := parent.Mkdir(name, 0700); err != nil && !errors.Is(err, os.ErrExist) {
		return nil, errors.New("modelpack: cannot create staging directory")
	}
	i, err := parent.Lstat(name)
	if err != nil || !i.IsDir() || i.Mode()&os.ModeSymlink != 0 || i.Mode().Perm() != 0700 {
		return nil, errors.New("modelpack: staging directory must be private and not a symlink")
	}
	root, err := parent.OpenRoot(name)
	if err != nil {
		return nil, errors.New("modelpack: cannot open staging directory")
	}
	defer root.Close()
	lock, err := lockStaging(root, i)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	// Persist the staging root entry, including on retry after a prior sync
	// failure. Root fsync cannot persist its own name in the parent directory.
	if err := syncDirectory(parent, "."); err != nil {
		return nil, err
	}
	want, dirs := localFileSet(in.files)
	initial, err := inventoryLocal(ctx, root, want, dirs, false)
	if err != nil {
		return nil, err
	}
	buf := make([]byte, 256<<10)
	// Reject a corrupt existing set before downloading any new bytes.
	for _, f := range in.files {
		if before, ok := initial[f.Path]; ok {
			if err := verifyLocalFile(ctx, root, f, before, buf); err != nil {
				return nil, err
			}
		}
	}
	var downloaded int64
	if workers > 1 {
		pending := make([]config.ManifestFile, 0, len(in.files))
		for _, f := range in.files {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			if _, ok := initial[f.Path]; ok {
				continue
			}
			if err := root.MkdirAll(path.Dir(f.Path), 0700); err != nil {
				return nil, errors.New("modelpack: cannot create model directory")
			}
			pending = append(pending, f)
		}
		// Start large shards early so they do not become a mostly idle tail.
		// Sort only missing work; the canonical manifest and proof stay unchanged.
		sort.SliceStable(pending, func(i, j int) bool { return pending[i].Bytes > pending[j].Bytes })
		downloaded, err = stageParallel(ctx, root, in.pack, pending, workers, download, syncDirectory)
		if err != nil {
			return nil, err
		}
	} else {
		for _, f := range in.files {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			if _, ok := initial[f.Path]; ok {
				continue
			}
			if err := root.MkdirAll(path.Dir(f.Path), 0700); err != nil {
				return nil, errors.New("modelpack: cannot create model directory")
			}
			n, err := stageFile(ctx, root, in.pack, f, options.MaxBytes-downloaded, download, buf, syncDirectory)
			downloaded += n
			if err != nil {
				return nil, err
			}
		}
	}
	if err := verifyLocalSet(ctx, root, in.files); err != nil {
		return nil, err
	}
	// Reused files may come from a prior run whose publication sync failed.
	// Retry durability before success even when this run downloaded nothing.
	for _, f := range in.files {
		if err := syncStageParents(root, f.Path, syncDirectory); err != nil {
			return nil, err
		}
	}
	current, err := os.Lstat(directory)
	if err != nil || !os.SameFile(i, current) || current.Mode().Perm() != 0700 {
		return nil, errors.New("modelpack: staging directory changed")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return &StageProof{LocalProof: localProof(in.pack, in.manifest, len(in.files)), DownloadedBytes: downloaded, ReusedFiles: len(initial)}, nil
}

func stageParallel(ctx context.Context, root *os.Root, pack *Pack, files []config.ManifestFile, workers int, download *weightDownload, syncDirectory func(*os.Root, string) error) (int64, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make(chan config.ManifestFile)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var downloaded int64
	var firstErr error
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, 256<<10)
			for f := range jobs {
				if ctx.Err() != nil {
					return
				}
				// Each file gets one attempt bounded to its manifest size plus
				// one byte. Their sum cannot exceed the validated global budget.
				n, err := stageFile(ctx, root, pack, f, f.Bytes+1, download, buf, syncDirectory)
				mu.Lock()
				downloaded += n
				if err != nil && firstErr == nil {
					firstErr = err
					cancel()
				}
				mu.Unlock()
				if err != nil {
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
	// Join every worker, including partial-file cleanup, before releasing the
	// staging lock or returning a proof/error to the caller.
	wg.Wait()
	if firstErr != nil {
		return downloaded, firstErr
	}
	return downloaded, ctx.Err()
}

func stageFile(ctx context.Context, root *os.Root, pack *Pack, expected config.ManifestFile, budget int64, download *weightDownload, buf []byte, syncDirectory func(*os.Root, string) error) (n int64, result error) {
	if budget < expected.Bytes+1 {
		return 0, errors.New("modelpack: download byte budget exhausted")
	}
	name := ".nib-part-" + rand.Text()
	f, err := root.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return 0, errors.New("modelpack: cannot create partial model file")
	}
	defer func() {
		if f != nil {
			_ = f.Close()
		}
		if err := root.Remove(name); err != nil {
			result = errors.New("modelpack: partial file cleanup failed; inspect staging directory")
		} else if err := syncStageParents(root, name, syncDirectory); err != nil {
			result = err
		}
	}()
	body, err := download.open(ctx, pack, expected)
	if err != nil {
		return 0, err
	}
	defer body.Close()
	h := sha256.New()
	reader := &countedWeightReader{r: &localReader{ctx: ctx, r: io.LimitReader(body, expected.Bytes+1)}}
	_, err = io.CopyBuffer(io.MultiWriter(f, h), reader, buf)
	n = reader.n
	if err != nil {
		if ctx.Err() != nil {
			return n, ctx.Err()
		}
		return n, errors.New("modelpack: model download or disk write failed")
	}
	if n != expected.Bytes || fmt.Sprintf("%x", h.Sum(nil)) != expected.SHA256 {
		return n, errors.New("modelpack: downloaded size or SHA-256 mismatch")
	}
	if err := ctx.Err(); err != nil {
		return n, err
	}
	if err := f.Sync(); err != nil {
		return n, errors.New("modelpack: model file sync failed")
	}
	if err := f.Close(); err != nil {
		f = nil
		return n, errors.New("modelpack: model file close failed")
	}
	f = nil
	// Hard-link publication is atomic and, unlike Rename, never overwrites a
	// destination created by a competing writer. Remove our temporary name only.
	if err := root.Link(name, expected.Path); err != nil {
		return n, errors.New("modelpack: exclusive model file publication failed")
	}
	return n, syncStageParents(root, expected.Path, syncDirectory)
}

// Persist publication and newly created containing directories, deepest first.
// Failure after Link leaves a complete file that will be rehashed on restart.
func syncStageParents(root *os.Root, name string, syncDirectory func(*os.Root, string) error) error {
	for d := path.Dir(name); ; d = path.Dir(d) {
		if err := syncDirectory(root, d); err != nil {
			return err
		}
		if d == "." {
			return nil
		}
	}
}

func syncStageDirectory(root *os.Root, name string) error {
	f, err := openLocalWeight(root, name)
	if err != nil {
		return errors.New("modelpack: cannot open model directory for sync")
	}
	i, err := f.Stat()
	if err != nil || !i.IsDir() {
		f.Close()
		return errors.New("modelpack: model directory changed before sync")
	}
	err = f.Sync()
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return errors.New("modelpack: model directory sync failed")
	}
	return nil
}

// Count bytes consumed from HTTP, including bytes returned alongside an error
// or a short disk write. This is not a TLS/network wire-byte accounting limit.
type countedWeightReader struct {
	r io.Reader
	n int64
}

func (r *countedWeightReader) Read(p []byte) (int, error) {
	n, err := r.r.Read(p)
	r.n += int64(n)
	return n, err
}

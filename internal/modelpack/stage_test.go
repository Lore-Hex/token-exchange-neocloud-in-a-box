// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func stageFixture(t *testing.T) (string, string, map[string]string, StageOptions) {
	t.Helper()
	pack, source := localFixture(t)
	in, err := loadStage(pack)
	if err != nil {
		t.Fatal(err)
	}
	bodies := make(map[string]string)
	for _, f := range in.files {
		body, err := os.ReadFile(filepath.Join(source, f.Path))
		if err != nil {
			t.Fatal(err)
		}
		bodies[f.Path] = string(body)
	}
	return pack, filepath.Join(t.TempDir(), "staged"), bodies,
		StageOptions{MaxBytes: in.plan.MinimumBudgetBytes, Timeout: time.Minute}
}

func fakeWeights(t *testing.T, bodies map[string]string, calls *int) *weightDownload {
	t.Helper()
	return &weightDownload{client: &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		*calls++
		prefix := "/org/model/resolve/" + testRev + "/"
		if r.Method != "GET" || r.URL.Host != "huggingface.co" || !strings.HasPrefix(r.URL.Path, prefix) ||
			r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("Referer") != "" ||
			r.Header.Get("Accept-Encoding") != "identity" {
			t.Fatal("unexpected or credentialed weight request")
		}
		b, ok := bodies[strings.TrimPrefix(r.URL.Path, prefix)]
		if !ok {
			t.Fatal("unlisted file request")
		}
		return response(b), nil
	})}}
}

func TestStageCompleteAndResume(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	count := 0
	d := fakeWeights(t, bodies, &count)
	proof, err := stage(context.Background(), pack, dir, options, d)
	if err != nil {
		t.Fatal(err)
	}
	if count != 3 || proof.Files != 3 || proof.ReusedFiles != 0 || proof.DownloadedBytes != proof.VerifiedBytes {
		t.Fatal("incorrect transfer proof")
	}
	if _, err := CheckLocal(context.Background(), pack, dir); err != nil {
		t.Fatal(err)
	}
	for name := range bodies {
		i, err := os.Stat(filepath.Join(dir, name))
		if err != nil || i.Mode().Perm() != 0600 {
			t.Fatal("unsafe file permissions")
		}
	}
	proof, err = stage(context.Background(), pack, dir, options, d)
	if err != nil || proof.ReusedFiles != 3 || proof.DownloadedBytes != 0 || count != 3 {
		t.Fatal("verified files not reused")
	}
}

func TestStageFailureResumesOnlyCompleteFiles(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	count := 0
	d := fakeWeights(t, bodies, &count)
	base := d.client.Transport
	d.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		if strings.HasSuffix(r.URL.Path, "weight.safetensors") {
			return nil, errors.New("PRIVATE signed query")
		}
		return base.RoundTrip(r)
	})
	proof, err := stage(context.Background(), pack, dir, options, d)
	if proof != nil || err == nil || strings.Contains(err.Error(), "PRIVATE") {
		t.Fatal("failed download accepted or leaked")
	}
	if count != 2 {
		t.Fatal("completed prefix not downloaded")
	}
	d.client.Transport = base
	proof, err = stage(context.Background(), pack, dir, options, d)
	if err != nil || proof.ReusedFiles != 2 || count != 3 {
		t.Fatalf("resume failed: %v", err)
	}
}

func TestStageRejectsBeforeNetwork(t *testing.T) {
	for _, kind := range []string{"budget", "timeout-zero", "timeout-large", "relative", "noncanonical", "root-link", "permissions", "extra", "corrupt", "file-link", "directory-link", "locked", "cancel"} {
		t.Run(kind, func(t *testing.T) {
			pack, dir, _, options := stageFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if err := os.Mkdir(dir, 0700); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "budget":
				options.MaxBytes--
			case "timeout-zero":
				options.Timeout = 0
			case "timeout-large":
				options.Timeout = 25 * time.Hour
			case "relative":
				dir = "PRIVATE"
			case "noncanonical":
				dir += "/./"
			case "root-link":
				link := dir + "-link"
				if err := os.Symlink(dir, link); err != nil {
					t.Fatal(err)
				}
				dir = link
			case "permissions":
				if err := os.Chmod(dir, 0755); err != nil {
					t.Fatal(err)
				}
			case "extra":
				if err := os.WriteFile(filepath.Join(dir, "PRIVATE"), []byte("PRIVATE"), 0600); err != nil {
					t.Fatal(err)
				}
			case "corrupt":
				if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte(strings.Repeat("X", len("fixture content config.json"))), 0600); err != nil {
					t.Fatal(err)
				}
			case "file-link":
				if err := os.Symlink("/PRIVATE", filepath.Join(dir, "config.json")); err != nil {
					t.Fatal(err)
				}
			case "directory-link":
				if err := os.Symlink("/PRIVATE", filepath.Join(dir, "sub")); err != nil {
					t.Fatal(err)
				}
			case "locked":
				r, err := os.OpenRoot(dir)
				if err != nil {
					t.Fatal(err)
				}
				defer r.Close()
				i, err := os.Stat(dir)
				if err != nil {
					t.Fatal(err)
				}
				lock, err := lockStaging(r, i)
				if err != nil {
					t.Fatal(err)
				}
				defer lock.Close()
			case "cancel":
				cancel()
			}
			d := &weightDownload{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("network reached"); return nil, nil })}}
			proof, err := stage(ctx, pack, dir, options, d)
			if proof != nil || err == nil || strings.Contains(err.Error(), "PRIVATE") {
				t.Fatalf("unsafe staging accepted or leaked: %v", err)
			}
		})
	}
}

func TestStageCorruptNewBytesNeverPublished(t *testing.T) {
	for _, kind := range []string{"short", "long", "hash", "encoding", "length", "read-error", "cancel", "destination-race"} {
		t.Run(kind, func(t *testing.T) {
			pack, dir, bodies, options := stageFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			body := bodies["config.json"]
			d := &weightDownload{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
				res := response(body)
				switch kind {
				case "short":
					res = response(body[:len(body)-1])
				case "long":
					res = response(body + "extra")
				case "hash":
					res = response(strings.Repeat("x", len(body)))
				case "encoding":
					res.Header.Set("Content-Encoding", "gzip")
				case "length":
					res.ContentLength = int64(len(body) + 1)
				case "read-error":
					res.Body = io.NopCloser(failingBody{})
				case "cancel":
					cancel()
				case "destination-race":
					if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte("preserve"), 0600); err != nil {
						t.Fatal(err)
					}
				}
				return res, nil
			})}}
			proof, err := stage(ctx, pack, dir, options, d)
			if proof != nil || err == nil || strings.Contains(err.Error(), "PRIVATE") {
				t.Fatal("corrupt bytes accepted or leaked")
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				t.Fatal(err)
			}
			if kind == "destination-race" {
				b, err := os.ReadFile(filepath.Join(dir, "config.json"))
				if err != nil || string(b) != "preserve" {
					t.Fatal("overwrote competing file")
				}
				if len(entries) != 1 {
					t.Fatal("partial file leaked")
				}
			} else if len(entries) != 0 {
				t.Fatal("bad content or temporary file retained")
			}
		})
	}
}

type failingBody struct{}

func (failingBody) Read([]byte) (int, error) { return 0, errors.New("PRIVATE transport details") }

func TestStageCLIPlanIsOfflineAndLimitsAreExplicit(t *testing.T) {
	pack, dir, _, _ := stageFixture(t)
	var out, errs bytes.Buffer
	if Run(context.Background(), []string{"stage", pack}, &out, &errs) != 0 {
		t.Fatal(errs.String())
	}
	var plan StagePlan
	if json.Unmarshal(out.Bytes(), &plan) != nil || plan.Files != 3 || plan.MinimumBudgetBytes != plan.TotalBytes+3 {
		t.Fatal("incorrect offline plan")
	}
	if _, err := os.Stat(dir); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("planning wrote directory")
	}
	for _, args := range [][]string{
		{"stage"}, {"stage", "--unknown=PRIVATE", pack}, {"stage", pack, dir}, {"stage", "--max-bytes", "1", pack},
		{"stage", "--download", pack, dir}, {"stage", "--download", "--max-bytes", "999", pack, dir},
		{"stage", "--download", "--max-bytes", "1", "--timeout", "1m", pack, dir},
		{"stage", "--workers", "4", pack},
		{"stage", "--download", "--max-bytes", "999", "--timeout", "1m", "--workers", "0", pack, dir},
		{"stage", "--download", "--max-bytes", "999", "--timeout", "1m", "--workers", "9", pack, dir},
	} {
		out.Reset()
		errs.Reset()
		if Run(context.Background(), args, &out, &errs) == 0 || strings.Contains(out.String()+errs.String(), "PRIVATE") {
			t.Fatal("CLI accepted unsafe args or leaked")
		}
	}
	if _, err := os.Stat(dir); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("invalid budget created directory")
	}
}

func TestStageProductionTransportHasNoAmbientSecrets(t *testing.T) {
	t.Setenv("HTTPS_PROXY", "http://PRIVATE.invalid")
	t.Setenv("HF_TOKEN", "PRIVATE")
	d := newWeightDownload()
	defer d.client.CloseIdleConnections()
	tr := d.client.Transport.(*http.Transport)
	if tr.Proxy != nil || !tr.DisableCompression || tr.MaxResponseHeaderBytes != 64<<10 || tr.ResponseHeaderTimeout <= 0 ||
		d.client.Jar != nil || d.client.Timeout != 0 {
		t.Fatal("unsafe download transport defaults")
	}
}

func TestStageContractIsFrozenBeforeDownload(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	before, err := PlanStage(pack)
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	d := fakeWeights(t, bodies, &count)
	base := d.client.Transport
	d.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		// A metadata replacement must not silently change the contract used by
		// the final check. Subsequent invocations still reject the changed file.
		if err := os.WriteFile(filepath.Join(pack, "files.sha256"), []byte("invalid"), 0600); err != nil {
			t.Fatal(err)
		}
		return base.RoundTrip(r)
	})
	proof, err := stage(context.Background(), pack, dir, options, d)
	if err != nil || proof.ManifestSHA256 != before.ManifestSHA256 || proof.Files != before.Files {
		t.Fatalf("contract reloaded during staging: %v", err)
	}
	if _, err := CheckLocal(context.Background(), pack, dir); err == nil {
		t.Fatal("subsequent verifier accepted invalid metadata")
	}
}

func TestStageBodyBoundsAndPartialCleanup(t *testing.T) {
	for _, kind := range []string{"oversize", "bytes-and-error", "midstream-cancel", "budget"} {
		t.Run(kind, func(t *testing.T) {
			pack, dir, _, options := stageFixture(t)
			in, err := loadStage(pack)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.Mkdir(dir, 0700); err != nil {
				t.Fatal(err)
			}
			root, err := os.OpenRoot(dir)
			if err != nil {
				t.Fatal(err)
			}
			defer root.Close()
			ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
			defer cancel()
			f := in.files[0]
			consumed := int64(0)
			body := weightReaderFunc(func(b []byte) (int, error) {
				if kind == "budget" {
					t.Fatal("read beyond byte budget")
				}
				if kind == "midstream-cancel" {
					cancel()
				}
				n := len(b)
				if kind != "oversize" {
					n = 1
				}
				clear(b[:n])
				consumed += int64(n)
				if kind == "bytes-and-error" {
					return n, errors.New("PRIVATE stream error")
				}
				return n, nil
			})
			d := &weightDownload{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
				if kind == "budget" {
					t.Fatal("network reached without budget")
				}
				res := response("")
				res.ContentLength = -1
				res.Body = io.NopCloser(body)
				return res, nil
			})}}
			if kind == "budget" {
				options.MaxBytes = f.Bytes
			}
			n, err := stageFile(ctx, root, in.pack, f, options.MaxBytes, d, make([]byte, 4), syncStageDirectory)
			if err == nil || strings.Contains(err.Error(), "PRIVATE") || n != consumed || consumed > f.Bytes+1 {
				t.Fatal("incorrect byte accounting or error leak")
			}
			if kind == "oversize" && n != f.Bytes+1 {
				t.Fatal("did not read overflow sentinel")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 {
				t.Fatal("partial file retained")
			}
		})
	}
}

type weightReaderFunc func([]byte) (int, error)

func (f weightReaderFunc) Read(b []byte) (int, error) { return f(b) }

func TestStagePreservesCrashLeftovers(t *testing.T) {
	pack, dir, _, options := stageFixture(t)
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	leftover := filepath.Join(dir, ".nib-part-from-previous-process")
	if err := os.WriteFile(leftover, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	d := &weightDownload{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
		t.Fatal("download started with unexplained file")
		return nil, nil
	})}}
	if proof, err := stage(context.Background(), pack, dir, options, d); err == nil || proof != nil {
		t.Fatal("crash leftover accepted")
	}
	if b, err := os.ReadFile(leftover); err != nil || string(b) != "preserve" {
		t.Fatal("removed another process's file")
	}
}

func TestStageDirectorySyncFailsClosed(t *testing.T) {
	r, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if err := syncStageParents(r, "missing/file", syncStageDirectory); err == nil {
		t.Fatal("missing directory synced")
	}
	f, err := r.Create("regular")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	if err := syncStageParents(r, "regular/file", syncStageDirectory); err == nil {
		t.Fatal("non-directory synced")
	}
	r.Close()
	if err := syncStageParents(r, "file", syncStageDirectory); err == nil {
		t.Fatal("closed root synced")
	}
}

func TestStageRejectsBrokenManifestWithoutWrites(t *testing.T) {
	pack, dir, _, options := stageFixture(t)
	if err := os.WriteFile(filepath.Join(pack, "files.sha256"), bytes.Repeat([]byte("x"), config.MaxManifestBytes+1), 0600); err != nil {
		t.Fatal(err)
	}
	d := &weightDownload{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
		t.Fatal("network reached with broken manifest")
		return nil, nil
	})}}
	if proof, err := stage(context.Background(), pack, dir, options, d); err == nil || proof != nil {
		t.Fatal("broken manifest accepted")
	}
	if _, err := os.Stat(dir); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("broken manifest created staging directory")
	}
}

func TestStageRetriesParentSyncBeforeNetwork(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	parentInfo, err := os.Stat(filepath.Dir(dir))
	if err != nil {
		t.Fatal(err)
	}
	count, parentCalls := 0, 0
	d := fakeWeights(t, bodies, &count)
	fail := true
	syncDir := func(root *os.Root, name string) error {
		i, err := root.Stat(name)
		if err != nil {
			t.Fatal(err)
		}
		if os.SameFile(i, parentInfo) {
			parentCalls++
			if count != 0 {
				t.Fatal("staging parent sync ran after downloads")
			}
			if fail {
				return errors.New("modelpack: injected directory sync failure")
			}
		}
		return syncStageDirectory(root, name)
	}
	if proof, err := stageWithSync(context.Background(), pack, dir, options, d, syncDir); proof != nil || err == nil || count != 0 || parentCalls != 1 {
		t.Fatal("parent sync failure ignored")
	}
	fail = false
	if proof, err := stageWithSync(context.Background(), pack, dir, options, d, syncDir); err != nil || proof.Files != 3 || count != 3 || parentCalls != 2 {
		t.Fatalf("parent sync retry failed: %v", err)
	}
}

func TestStageRetriesPublicationSyncForReusedFiles(t *testing.T) {
	for _, allReused := range []bool{false, true} {
		t.Run(fmt.Sprintf("all-reused-%t", allReused), func(t *testing.T) {
			pack, dir, bodies, options := stageFixture(t)
			count := 0
			d := fakeWeights(t, bodies, &count)
			if allReused {
				if _, err := stage(context.Background(), pack, dir, options, d); err != nil {
					t.Fatal(err)
				}
			}
			failed := 0
			syncDir := func(root *os.Root, name string) error {
				if name == "sub" {
					failed++
					return errors.New("modelpack: injected publication sync failure")
				}
				return syncStageDirectory(root, name)
			}
			if proof, err := stageWithSync(context.Background(), pack, dir, options, d, syncDir); proof != nil || err == nil || failed != 1 {
				t.Fatal("publication sync failure ignored")
			}
			expectedReused := 2
			if allReused {
				expectedReused = 3
			}
			if count != expectedReused {
				t.Fatal("unexpected transfer count")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 2 {
				t.Fatal("partial filename not cleaned up after publication failure")
			}
			synced := 0
			syncDir = func(root *os.Root, name string) error {
				if name == "sub" {
					synced++
				}
				return syncStageDirectory(root, name)
			}
			proof, err := stageWithSync(context.Background(), pack, dir, options, d, syncDir)
			if err != nil || proof.ReusedFiles != expectedReused || count != 3 || synced == 0 {
				t.Fatalf("reused publication not synced on retry: %v", err)
			}
		})
	}
}

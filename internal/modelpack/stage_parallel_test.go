// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func parallelWeights(bodies map[string]string, before func(*http.Request) error) *weightDownload {
	return &weightDownload{client: &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		if before != nil {
			if err := before(r); err != nil {
				return nil, err
			}
		}
		name := strings.TrimPrefix(r.URL.Path, "/org/model/resolve/"+testRev+"/")
		body, ok := bodies[name]
		if !ok {
			return nil, errors.New("unlisted request")
		}
		return response(body), nil
	})}}
}

func TestStageLargestMissingFilesStartFirst(t *testing.T) {
	for _, tc := range []struct {
		name    string
		workers int
		reuse   bool
		first   []string
	}{
		{"parallel", 2, false, []string{"z-large", "zz-largest"}},
		{"serial-unchanged", 1, false, []string{"a-small"}},
		{"verified-reuse", 2, true, []string{"b-medium", "z-large"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := &Manifest{Repository: "org/model", Revision: testRev}
			bodies := map[string]string{"a-small": "a", "b-medium": "bbbb", "z-large": "zzzzzzzz", "zz-largest": "largest-body"}
			for _, name := range []string{"a-small", "b-medium", "z-large", "zz-largest"} {
				body := bodies[name]
				m.Files = append(m.Files, File{Path: name, Bytes: int64(len(body)), SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(body)))})
				m.TotalBytes += int64(len(body))
			}
			pack, dir := writePack(t, m), filepath.Join(t.TempDir(), "staged")
			if tc.reuse {
				if err := os.Mkdir(dir, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, "zz-largest"), []byte(bodies["zz-largest"]), 0600); err != nil {
					t.Fatal(err)
				}
			}
			started, release := make(chan string, len(bodies)), make(chan struct{})
			var calls atomic.Int32
			d := parallelWeights(bodies, func(r *http.Request) error {
				calls.Add(1)
				started <- filepath.Base(r.URL.Path)
				select {
				case <-release:
					return nil
				case <-r.Context().Done():
					return r.Context().Err()
				}
			})
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			type result struct {
				proof *StageProof
				err   error
			}
			done := make(chan result, 1)
			go func() {
				proof, err := stage(ctx, pack, dir, StageOptions{MaxBytes: m.TotalBytes + int64(len(m.Files)), Timeout: 5 * time.Second, Workers: tc.workers}, d)
				done <- result{proof, err}
			}()
			first := make([]string, 0, tc.workers)
			for range tc.workers {
				select {
				case name := <-started:
					first = append(first, name)
				case <-ctx.Done():
					t.Fatal("initial downloads did not start")
				}
			}
			close(release)
			got := <-done
			slices.Sort(first)
			if !slices.Equal(first, tc.first) {
				t.Fatalf("first downloads %v, want %v", first, tc.first)
			}
			wantFiles, wantBytes, reused := len(bodies), m.TotalBytes, 0
			if tc.reuse {
				wantFiles--
				wantBytes -= int64(len(bodies["zz-largest"]))
				reused = 1
			}
			if got.err != nil || got.proof == nil || got.proof.Files != len(m.Files) || got.proof.DownloadedBytes != wantBytes || got.proof.ReusedFiles != reused || calls.Load() != int32(wantFiles) {
				t.Fatalf("staging proof or request count changed: proof=%+v err=%v calls=%d", got.proof, got.err, calls.Load())
			}
		})
	}
}

func TestStageParallelBoundAndProof(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	options.Workers = 2
	started, release := make(chan struct{}, 3), make(chan struct{})
	var active, peak, calls atomic.Int32
	d := parallelWeights(bodies, func(r *http.Request) error {
		n := active.Add(1)
		defer active.Add(-1)
		calls.Add(1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		started <- struct{}{}
		select {
		case <-release:
			return nil
		case <-r.Context().Done():
			return r.Context().Err()
		}
	})
	type result struct {
		proof *StageProof
		err   error
	}
	done := make(chan result, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() {
		proof, err := stage(ctx, pack, dir, options, d)
		done <- result{proof, err}
	}()
	for range 2 {
		select {
		case <-started:
		case <-ctx.Done():
			t.Fatal("downloads did not overlap")
		}
	}
	close(release)
	got := <-done
	if got.err != nil || got.proof == nil || peak.Load() != 2 || active.Load() != 0 || calls.Load() != 3 {
		t.Fatalf("bad parallel result: %v peak=%d", got.err, peak.Load())
	}
	options.Workers = 1
	serial, err := stage(context.Background(), pack, filepath.Join(t.TempDir(), "serial"), options, parallelWeights(bodies, nil))
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(got.proof)
	b, _ := json.Marshal(serial)
	if string(a) != string(b) {
		t.Fatal("parallel proof differs from single-worker proof")
	}
	options.Workers = 4
	resumed, err := stage(context.Background(), pack, dir, options, d)
	if err != nil || resumed.ReusedFiles != 3 || resumed.DownloadedBytes != 0 || calls.Load() != 3 {
		t.Fatalf("parallel resume fetched verified files: %v", err)
	}
}

func TestStageParallelFailureJoinsAndCleans(t *testing.T) {
	pack, dir, bodies, options := stageFixture(t)
	options.Workers = 2
	var calls, active atomic.Int32
	second := make(chan struct{})
	d := parallelWeights(bodies, func(r *http.Request) error {
		active.Add(1)
		defer active.Add(-1)
		if calls.Add(1) == 1 {
			select {
			case <-second:
				return errors.New("upstream failed")
			case <-r.Context().Done():
				return r.Context().Err()
			}
		}
		close(second)
		<-r.Context().Done()
		return r.Context().Err()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	proof, err := stage(ctx, pack, dir, options, d)
	if err == nil || proof != nil || active.Load() != 0 || calls.Load() != 2 {
		t.Fatal("failed parallel run did not join both workers")
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".nib-part-") {
			t.Fatal("partial file survived joined cancellation")
		}
	}
	proof, err = stage(context.Background(), pack, dir, options, parallelWeights(bodies, nil))
	if err != nil || proof.Files != 3 {
		t.Fatalf("failed run left lock or invalid files: %v", err)
	}
}

func TestStageParallelRejectsInvalidWorkers(t *testing.T) {
	for _, workers := range []int{-1, MaxStageWorkers + 1} {
		pack, dir, bodies, options := stageFixture(t)
		options.Workers = workers
		var calls atomic.Int32
		d := parallelWeights(bodies, func(*http.Request) error { calls.Add(1); return nil })
		proof, err := stage(context.Background(), pack, dir, options, d)
		if err == nil || proof != nil || calls.Load() != 0 {
			t.Fatal("invalid worker count admitted")
		}
		if _, err := os.Stat(dir); !os.IsNotExist(err) {
			t.Fatal("invalid worker count created a directory")
		}
	}
}

func TestStageMaximumConcurrencyPreservesBudgetProofAndResume(t *testing.T) {
	m := &Manifest{Repository: "org/model", Revision: testRev}
	bodies := make(map[string]string)
	for i := range MaxStageWorkers + 1 {
		name := fmt.Sprintf("part-%02d.safetensors", i)
		body := "synthetic " + name
		bodies[name] = body
		m.Files = append(m.Files, File{Path: name, Bytes: int64(len(body)), SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(body)))})
		m.TotalBytes += int64(len(body))
	}
	pack, dir := writePack(t, m), filepath.Join(t.TempDir(), "staged")
	options := StageOptions{MaxBytes: m.TotalBytes + int64(len(m.Files)), Timeout: 5 * time.Second, Workers: MaxStageWorkers}
	started, release := make(chan struct{}, len(m.Files)), make(chan struct{})
	var active, peak, calls atomic.Int32
	d := parallelWeights(bodies, func(r *http.Request) error {
		n := active.Add(1)
		defer active.Add(-1)
		calls.Add(1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		started <- struct{}{}
		select {
		case <-release:
			return nil
		case <-r.Context().Done():
			return r.Context().Err()
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	type result struct {
		proof *StageProof
		err   error
	}
	done := make(chan result, 1)
	go func() {
		proof, err := stage(ctx, pack, dir, options, d)
		done <- result{proof, err}
	}()
	for range MaxStageWorkers {
		select {
		case <-started:
		case <-ctx.Done():
			t.Fatal("maximum workers did not overlap")
		}
	}
	close(release)
	got := <-done
	if got.err != nil || got.proof == nil || peak.Load() != MaxStageWorkers || active.Load() != 0 || calls.Load() != int32(len(m.Files)) {
		t.Fatalf("incorrect maximum worker result: %v peak=%d", got.err, peak.Load())
	}
	if got.proof.DownloadedBytes != m.TotalBytes || got.proof.DownloadedBytes > options.MaxBytes {
		t.Fatal("maximum concurrency changed the body budget")
	}
	options.Workers = 1
	serial, err := stage(ctx, pack, filepath.Join(t.TempDir(), "serial"), options, parallelWeights(bodies, nil))
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(got.proof)
	b, _ := json.Marshal(serial)
	if string(a) != string(b) {
		t.Fatal("maximum-worker proof changed")
	}
	options.Workers = MaxStageWorkers
	resumed, err := stage(ctx, pack, dir, options, d)
	if err != nil || resumed.ReusedFiles != len(m.Files) || resumed.DownloadedBytes != 0 || calls.Load() != int32(len(m.Files)) {
		t.Fatalf("maximum-worker resume refetched complete files: %v", err)
	}
}

func TestStageParallelRejectsOversizedAndCorruptBodies(t *testing.T) {
	for _, body := range []string{"corrupt", strings.Repeat("x", 10000)} {
		pack, dir, bodies, options := stageFixture(t)
		options.Workers = 4
		for name := range bodies {
			bodies[name] = body
		}
		proof, err := stage(context.Background(), pack, dir, options, parallelWeights(bodies, nil))
		if err == nil || proof != nil {
			t.Fatal("invalid parallel bodies accepted")
		}
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), ".nib-part-") {
				t.Fatal("invalid body partial survived")
			}
		}
	}
}

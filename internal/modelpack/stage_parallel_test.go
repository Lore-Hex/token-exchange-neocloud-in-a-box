// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
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
	for _, workers := range []int{-1, 5} {
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

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func TestParallelLocalProofMatchesSerialAndCLI(t *testing.T) {
	p, dir := localFixture(t)
	want, err := CheckLocal(context.Background(), p, dir)
	if err != nil {
		t.Fatal(err)
	}
	for workers := 1; workers <= MaxLocalWorkers; workers++ {
		got, err := CheckLocalWithWorkers(context.Background(), p, dir, workers)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("workers=%d proof differs: %v", workers, err)
		}
		var out, errs bytes.Buffer
		if Run(context.Background(), []string{"check-local", "--workers", fmt.Sprint(workers), p, dir}, &out, &errs) != 0 || errs.Len() != 0 {
			t.Fatal("parallel CLI failed")
		}
	}
	for _, workers := range []int{-1, 0, 9, 1000000} {
		if proof, err := CheckLocalWithWorkers(context.Background(), "PRIVATE", "PRIVATE", workers); proof != nil || err == nil {
			t.Fatal("invalid worker bound accepted")
		}
	}
	for _, args := range [][]string{{"--workers", "0"}, {"--workers", "9"}, {"--workers", "PRIVATE"}, {"--unknown"}, {"--workers"}} {
		var out, errs bytes.Buffer
		if Run(context.Background(), append([]string{"check-local"}, args...), &out, &errs) != 2 || bytes.Contains(errs.Bytes(), []byte("PRIVATE")) {
			t.Fatal("invalid CLI accepted or leaked")
		}
	}
}

func TestLocalChecksBoundConcurrencyAndJoinOnSuccessFailureAndCancel(t *testing.T) {
	for _, mode := range []string{"success", "failure", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			files := make([]config.ManifestFile, 32)
			for i := range files {
				files[i].Path = fmt.Sprint(i)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			started := make(chan string, len(files))
			release := make(chan struct{})
			done := make(chan error, 1)
			var mu sync.Mutex
			seen := map[string]bool{}
			buffers := map[*byte]bool{}
			active, maximum, finished := 0, 0, 0
			failure := errors.New("fixture failure")
			go func() {
				done <- localChecks(ctx, files, 8, func(ctx context.Context, f config.ManifestFile, buf []byte) error {
					mu.Lock()
					if seen[f.Path] || len(buf) != 256<<10 {
						mu.Unlock()
						return errors.New("duplicate or unbounded buffer")
					}
					seen[f.Path], buffers[&buf[0]] = true, true
					active++
					maximum = max(maximum, active)
					mu.Unlock()
					defer func() { mu.Lock(); active--; finished++; mu.Unlock() }()
					started <- f.Path
					if mode == "success" || (mode == "failure" && f.Path == "0") {
						select {
						case <-release:
							if mode == "failure" {
								return failure
							}
							return nil
						case <-ctx.Done():
							return ctx.Err()
						}
					}
					<-ctx.Done()
					return ctx.Err()
				})
			}()
			for range 8 {
				select {
				case <-started:
				case <-time.After(5 * time.Second):
					cancel()
					t.Fatal("workers failed to reach barrier")
				}
			}
			if mode == "cancel" {
				cancel()
			} else {
				close(release)
			}
			select {
			case err := <-done:
				if (mode == "success" && err != nil) || (mode == "failure" && !errors.Is(err, failure)) || (mode == "cancel" && !errors.Is(err, context.Canceled)) {
					t.Fatalf("wrong result: %v", err)
				}
			case <-time.After(5 * time.Second):
				cancel()
				t.Fatal("workers did not join")
			}
			mu.Lock()
			defer mu.Unlock()
			if active != 0 || maximum != 8 || len(buffers) != 8 || finished != len(seen) || (mode == "success" && finished != len(files)) {
				t.Fatal("concurrency/buffer/join bound violated")
			}
		})
	}
}

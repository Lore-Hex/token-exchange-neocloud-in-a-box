// SPDX-License-Identifier: Apache-2.0

// packcheck is a fixed-input, bounded, public metadata-only generation and live
// verification command. Commit its source before running it. It never overwrites
// a manifest or evidence directory and never reads user credentials.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/evidence"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/modelpack"
)

const upstreamImage = "vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b"
const tinfoilImage = "ghcr.io/tinfoilsh/confidential-deepseek-v4-1-flash@sha256:989d5d0d8c02b240254f39ada9ac4e898c430ac955b12feb68070d41bb12b639"

var packs = []struct {
	dir, repo, revision string
	total               int64
}{
	{"models/deepseek-v4.1-flash-1017/1", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "1bae0e1088993b9b82de08f59de5be5a34e42170", 510311045295},
	{"models/glm-5.3/1", "zai-org/GLM-5.3", "ba32f8c19c8738e2c57c71798c601d477a7bf844", 755663689302},
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "packcheck failed; inspect generated evidence if present")
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) != 1 || (args[0] != "generate" && args[0] != "check") {
		return errors.New("usage: go run ./models/packcheck generate|check")
	}
	mode := args[0]
	// A real repository root and a clean checkout bind code and inputs to git_sha.
	if _, err := os.Stat("AGENTS.md"); err != nil {
		return errors.New("repository root required")
	}
	status, err := exec.Command("git", "status", "--porcelain").Output()
	if err != nil || len(status) != 0 {
		return errors.New("clean committed checkout required")
	}
	sha, err := exec.Command("git", "rev-parse", "HEAD").Output()
	commit := strings.TrimSpace(string(sha))
	if err != nil || !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(commit) {
		return errors.New("commit unavailable")
	}
	start := time.Now().UTC()
	dir := filepath.Join("evidence", "p0", start.Format("2006-01-02T150405Z")+"-model-pack-"+mode)
	if err := os.Mkdir(dir, 0755); err != nil {
		return errors.New("evidence destination exists or unavailable")
	}
	var output bytes.Buffer
	var report struct {
		Sources           []sourceProof           `json:"sources"`
		Models            []*modelpack.Manifest   `json:"models"`
		Images            []*modelpack.ImageProof `json:"images"`
		ManifestSHA256    map[string]string       `json:"manifest_sha256"`
		Mode              string                  `json:"mode"`
		HardwareValidated bool                    `json:"hardware_validated"`
	}
	report.Mode = mode
	report.ManifestSHA256 = make(map[string]string)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()
	workErr := func() error {
		var err error
		report.Sources, err = auditSources(ctx, sourceClient())
		if err != nil {
			return err
		}
		fmt.Fprintln(&output, "Immutable engine sources hashed; global VLLM_DISABLE_UVA requires Tinfoil's patch, absent from the upstream base.")
		for _, image := range []string{upstreamImage, tinfoilImage} {
			proof, err := modelpack.VerifyImage(ctx, image)
			if err != nil {
				return err
			}
			report.Images = append(report.Images, proof)
			fmt.Fprintf(&output, "Registry manifest digest verified: %s (%d bytes); no layers downloaded.\n", proof.Image, proof.ManifestBytes)
		}
		for _, p := range packs {
			m, err := modelpack.Generate(ctx, p.repo, p.revision)
			if err != nil {
				return err
			}
			if m.TotalBytes != p.total {
				return errors.New("approved all-file total differs")
			}
			b, err := m.MarshalText()
			if err != nil {
				return err
			}
			path := filepath.Join(p.dir, "files.sha256")
			if mode == "generate" {
				if err := writeExclusive(path, b); err != nil {
					return err
				}
			} else {
				// Exercise the public API used by nib model check-remote as well
				// as recording the generated manifest and its transfer count.
				if err := modelpack.CheckRemote(ctx, p.dir); err != nil {
					return err
				}
				pack, manifest, err := modelpack.Load(p.dir)
				if err != nil {
					return err
				}
				if pack.Repository != p.repo || pack.Revision != p.revision || pack.TotalBytes != p.total || !bytes.Equal(manifest, b) {
					return errors.New("pack or manifest differs from approved pins")
				}
				for _, profile := range pack.EngineProfiles {
					if profile.Image != upstreamImage {
						return errors.New("profile differs from verified upstream image")
					}
				}
			}
			report.Models = append(report.Models, m)
			report.ManifestSHA256[p.dir] = fmt.Sprintf("%x", sha256.Sum256(b))
			fmt.Fprintf(&output, "%s@%s: %d files, %d all-file bytes, %d metadata/non-LFS bytes fetched.\n", p.repo, p.revision, len(m.Files), m.TotalBytes, m.DownloadedBytes)
		}
		return nil
	}()
	result := "pass"
	if workErr != nil {
		result = "fail"
		fmt.Fprintln(&output, "Metadata verification failed:", workErr)
	}
	fmt.Fprintln(&output, "Software metadata check only; no weights, container layers, serving or hardware validation.")
	if mode == "check" {
		fmt.Fprintln(&output, "CheckRemote repeats the bounded HF fetch; reported model transfer bytes cover Generate only.")
	}
	runRecord := map[string]any{
		"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": commit, "command": "go run ./models/packcheck " + mode,
		"started_utc": start.Format(time.RFC3339Nano), "ended_utc": time.Now().UTC().Format(time.RFC3339Nano),
		"host":   map[string]any{"provider": "local", "machine": runtime.GOOS + "/" + runtime.GOARCH, "gpus": "none", "driver": nil, "cuda": nil},
		"result": result, "execution": "software", "hardware_validated": false,
		"notes": "Public metadata only. LFS hashes are remote assertions, not downloaded weight verification. Empty verity roots remain non-deployable. No credentials supplied; registry tokens are anonymous public pull tokens kept in memory.",
	}
	runJSON, _ := json.MarshalIndent(runRecord, "", "  ")
	if err := config.ValidateEvidence(runJSON); err != nil {
		return errors.New("invalid evidence record")
	}
	reportJSON, _ := json.MarshalIndent(report, "", "  ")
	// Match scripts/scrub-secrets.sh's library. Diagnostics are static; this
	// additional deny list removes local identity and credential markers.
	deny := "Authorization:\nBearer \n"
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		deny += home + "\n"
	}
	if cwd, err := os.Getwd(); err == nil {
		deny += cwd + "\n"
	}
	var clean bytes.Buffer
	if err := evidence.Scrub(strings.NewReader(deny), &output, &clean); err != nil {
		return errors.New("output scrub failed")
	}
	for _, f := range []struct {
		name string
		data []byte
	}{{"run.json", append(runJSON, '\n')}, {"report.json", append(reportJSON, '\n')}, {"output.log", clean.Bytes()}} {
		if err := writeExclusive(filepath.Join(dir, f.name), f.data); err != nil {
			return err
		}
	}
	fmt.Print(clean.String())
	fmt.Println("Evidence:", dir)
	return workErr
}

func writeExclusive(path string, b []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return errors.New("output exists or cannot be created")
	}
	_, writeErr := f.Write(b)
	closeErr := f.Close()
	if writeErr != nil || closeErr != nil {
		return errors.New("output write failed")
	}
	return nil
}

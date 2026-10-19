// SPDX-License-Identifier: Apache-2.0

// sourceaudit records the P0.T4 source contradiction without generating packs.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const responseLimit = 4 << 20

var errBlocked = errors.New("sourceaudit: P0.T4 acceptance blocked; see generated evidence")

type source struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Bytes  int    `json:"bytes"`
}

type model struct {
	Repository       string `json:"repository"`
	Revision         string `json:"revision"`
	PlannedBytes     int64  `json:"planned_total_bytes"`
	FileCount        int    `json:"file_count"`
	WeightCount      int    `json:"safetensors_count"`
	TotalBytes       int64  `json:"all_file_bytes"`
	WeightBytes      int64  `json:"safetensors_bytes"`
	OtherBytes       int64  `json:"other_file_bytes"`
	AllLFSHasSHA256  bool   `json:"all_lfs_have_sha256"`
	PlanMatchesFiles bool   `json:"plan_matches_all_files"`
}

type treeEntry struct {
	Type string `json:"type"`
	Path string `json:"path"`
	Size *int64 `json:"size"`
	LFS  *struct {
		OID  string `json:"oid"`
		Size *int64 `json:"size"`
	} `json:"lfs"`
}

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "usage: ./build/sourceaudit (no arguments)")
		os.Exit(2)
	}
	out := "evidence/p0/" + time.Now().UTC().Format("2006-01-02") + "-model-pack-source-audit"
	if err := run(out); err != nil {
		// Never print transport errors, response bodies, headers or URLs.
		if !errors.Is(err, errBlocked) {
			fmt.Fprintln(os.Stderr, err)
		}
		os.Exit(1)
	}
}

func run(out string) error {
	if err := os.MkdirAll(filepath.Dir(out), 0755); err != nil {
		return errors.New("sourceaudit: cannot create evidence parent")
	}
	if err := os.Mkdir(out, 0755); err != nil {
		return errors.New("sourceaudit: evidence directory must be new")
	}
	started := time.Now().UTC()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	git, err := exec.CommandContext(ctx, "git", "rev-parse", "HEAD").Output()
	if err != nil {
		return errors.New("sourceaudit: cannot determine repository commit")
	}
	status, err := exec.CommandContext(ctx, "git", "status", "--porcelain", "--", "models/sourceaudit").Output()
	if err != nil || len(status) != 0 {
		return errors.New("sourceaudit: commit audit sources before collecting evidence")
	}
	models := []model{
		{Repository: "deepseek-ai/DeepSeek-V4.1-Flash-1017", Revision: "1bae0e1088993b9b82de08f59de5be5a34e42170", PlannedBytes: 510296708312},
		{Repository: "zai-org/GLM-5.3", Revision: "ba32f8c19c8738e2c57c71798c601d477a7bf844", PlannedBytes: 755632050320},
	}
	var sources []source
	var log strings.Builder
	for i := range models {
		m := &models[i]
		base := "https://huggingface.co/api/models/" + m.Repository
		u := base + "/revision/" + m.Revision
		body, s, err := fetch(ctx, "pinned model identity", u)
		if err != nil {
			return err
		}
		sources = append(sources, s)
		var identity struct {
			ID  string `json:"id"`
			SHA string `json:"sha"`
		}
		if json.Unmarshal(body, &identity) != nil || identity.ID != m.Repository || identity.SHA != m.Revision {
			return errors.New("sourceaudit: pinned repository identity mismatch")
		}
		u = base + "/tree/" + m.Revision + "?recursive=true&limit=1000"
		body, s, err = fetch(ctx, "recursive pinned model tree", u)
		if err != nil {
			return err
		}
		sources = append(sources, s)
		if err := summarize(body, m); err != nil {
			return err
		}
		if m.WeightBytes != m.PlannedBytes || m.PlanMatchesFiles {
			return errors.New("sourceaudit: sources no longer reproduce this recorded contradiction")
		}
		fmt.Fprintf(&log, "%s revision=%s files=%d all_file_bytes=%d safetensors_bytes=%d planned_total_bytes=%d matches_all_files=%t\n", m.Repository, m.Revision, m.FileCount, m.TotalBytes, m.WeightBytes, m.PlannedBytes, m.PlanMatchesFiles)
	}
	for _, s := range []source{
		{Name: "vLLM DeepSeek recipe", URL: "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml"},
		{Name: "vLLM GLM recipe", URL: "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml"},
		{Name: "SGLang DeepSeek cookbook", URL: "https://raw.githubusercontent.com/sgl-project/sglang/299da60131dbbd9d3be0e1b2463b1f82cd984729/docs/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.mdx"},
		{Name: "SGLang GLM cookbook", URL: "https://raw.githubusercontent.com/sgl-project/sglang/299da60131dbbd9d3be0e1b2463b1f82cd984729/docs/cookbook/autoregressive/GLM/GLM-5.3.mdx"},
		{Name: "Tinfoil B200 main configuration", URL: "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/803246156cb54952d0fc93a9fb66cdd4d87af2b3/tinfoil-config.b200.yml"},
		{Name: "Tinfoil main configuration", URL: "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/803246156cb54952d0fc93a9fb66cdd4d87af2b3/tinfoil-config.yml"},
		{Name: "Tinfoil B200 Dockerfile", URL: "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/803246156cb54952d0fc93a9fb66cdd4d87af2b3/Dockerfile.b200"},
		{Name: "Tinfoil v0.0.7+b200 configuration", URL: "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml"},
	} {
		_, observed, err := fetch(ctx, s.Name, s.URL)
		if err != nil {
			return err
		}
		sources = append(sources, observed)
		fmt.Fprintf(&log, "%s fetched bytes=%d sha256=%s\n", observed.Name, observed.Bytes, observed.SHA256)
	}
	log.WriteString("FAIL P0.T4: planned totals count safetensors only; complete file totals differ. No pack generated.\n")
	log.WriteString(errBlocked.Error() + "\n")
	downloaded := 0
	for _, s := range sources {
		downloaded += s.Bytes
	}
	report := struct {
		Status          string   `json:"status"`
		GoVersion       string   `json:"go_version"`
		ResponseLimit   int      `json:"max_response_bytes"`
		DownloadedBytes int      `json:"downloaded_bytes"`
		Models          []model  `json:"models"`
		Sources         []source `json:"sources"`
	}{"blocked: total_bytes contract contradicts complete manifests", runtime.Version(), responseLimit, downloaded, models, sources}
	run := map[string]any{
		"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": strings.TrimSpace(string(git)),
		"command":     "./build/sourceaudit",
		"started_utc": started.Format(time.RFC3339), "ended_utc": time.Now().UTC().Format(time.RFC3339),
		"host":   map[string]any{"provider": "local", "machine": runtime.GOOS + "/" + runtime.GOARCH, "gpus": "none", "driver": nil, "cuda": nil},
		"result": "fail", "execution": "software", "hardware_validated": false,
		"notes": "Source audit completed; P0.T4 acceptance blocked by weights-only planned totals. No model weights, container layers or paid resources used.",
	}
	for name, data := range map[string]any{"run.json": run, "sources.json": report} {
		b, err := json.MarshalIndent(data, "", "  ")
		if err != nil {
			return errors.New("sourceaudit: cannot encode evidence")
		}
		if err := os.WriteFile(filepath.Join(out, name), append(b, '\n'), 0644); err != nil {
			return errors.New("sourceaudit: cannot write evidence")
		}
	}
	if err := os.WriteFile(filepath.Join(out, "output.log"), []byte(log.String()), 0644); err != nil {
		return errors.New("sourceaudit: cannot write evidence log")
	}
	fmt.Print(log.String())
	return errBlocked
}

func fetch(ctx context.Context, name, rawURL string) ([]byte, source, error) {
	s := source{Name: name, URL: rawURL}
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || (u.Host != "huggingface.co" && u.Host != "raw.githubusercontent.com") {
		return nil, s, errors.New("sourceaudit: source URL is not allowlisted")
	}
	client := http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect forbidden") }}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, s, errors.New("sourceaudit: cannot construct request")
	}
	req.Header.Set("Accept-Encoding", "identity")
	resp, err := client.Do(req)
	if err != nil {
		return nil, s, errors.New("sourceaudit: source request failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, s, fmt.Errorf("sourceaudit: source HTTP status %d", resp.StatusCode)
	}
	// This deliberately small audit is not a general manifest generator. Fail
	// closed on pagination instead of treating a partial tree as complete.
	if len(resp.Header.Values("Link")) != 0 {
		return nil, s, errors.New("sourceaudit: pagination present; audit cannot claim a complete tree")
	}
	if resp.ContentLength > responseLimit {
		return nil, s, errors.New("sourceaudit: response exceeds limit")
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, responseLimit+1))
	if err != nil || len(b) > responseLimit {
		return nil, s, errors.New("sourceaudit: response read failed or exceeded limit")
	}
	s.SHA256, s.Bytes = fmt.Sprintf("%x", sha256.Sum256(b)), len(b)
	return b, s, nil
}

func summarize(body []byte, m *model) error {
	var entries []treeEntry
	if json.Unmarshal(body, &entries) != nil || len(entries) == 0 {
		return errors.New("sourceaudit: invalid tree metadata")
	}
	m.AllLFSHasSHA256 = true
	seen := map[string]bool{}
	for _, f := range entries {
		if f.Path == "" || seen[f.Path] {
			return errors.New("sourceaudit: duplicate or missing tree path")
		}
		seen[f.Path] = true
		if f.Type == "directory" {
			continue
		}
		if f.Type != "file" || f.Size == nil || *f.Size < 0 || *f.Size > (1<<63-1)-m.TotalBytes {
			return errors.New("sourceaudit: invalid file metadata")
		}
		if f.LFS != nil {
			_, err := hex.DecodeString(f.LFS.OID)
			if err != nil || f.LFS.Size == nil || *f.LFS.Size != *f.Size || len(f.LFS.OID) != 64 || strings.Trim(f.LFS.OID, "0") == "" {
				return errors.New("sourceaudit: invalid LFS metadata")
			}
		}
		m.FileCount++
		m.TotalBytes += *f.Size
		if strings.HasSuffix(f.Path, ".safetensors") {
			m.WeightCount++
			m.WeightBytes += *f.Size
		}
	}
	if m.FileCount == 0 {
		return errors.New("sourceaudit: no files")
	}
	m.OtherBytes = m.TotalBytes - m.WeightBytes
	m.PlanMatchesFiles = m.TotalBytes == m.PlannedBytes
	return nil
}

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const sourceLimit = 4 << 20

var sourceURLs = []struct{ key, url string }{
	{"A", "https://raw.githubusercontent.com/vllm-project/vllm/" + upstreamRevision + "/vllm/engine/arg_utils.py"},
	{"F", "https://raw.githubusercontent.com/vllm-project/vllm/" + upstreamRevision + "/vllm/entrypoints/launchers/cli_args.py"},
	{"E", "https://raw.githubusercontent.com/vllm-project/vllm/" + upstreamRevision + "/vllm/envs.py"},
	{"C", "https://raw.githubusercontent.com/vllm-project/vllm/" + upstreamRevision + "/vllm/config/engram.py"},
	{"R", "https://api.github.com/repos/vllm-project/vllm/git/ref/tags/v0.32.0"},
	{"D", "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml"},
	{"G", "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml"},
}

type sourceProof struct {
	Key    string `json:"key"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Bytes  int    `json:"bytes"`
}

func sourceClient() *http.Client {
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.MaxResponseHeaderBytes = 64 << 10
	return &http.Client{Timeout: 30 * time.Second, Transport: tr, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

func auditSources(ctx context.Context, client *http.Client) ([]sourceProof, error) {
	var proofs []sourceProof
	for _, source := range sourceURLs {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, source.url, nil)
		if err != nil {
			return proofs, errors.New("source request construction failed")
		}
		req.Header.Set("Accept-Encoding", "identity")
		res, err := client.Do(req)
		if err != nil {
			return proofs, errors.New("source metadata request failed")
		}
		b, err := readSource(res)
		if err != nil {
			return proofs, err
		}
		if err := checkSource(source.key, b); err != nil {
			return proofs, err
		}
		proofs = append(proofs, sourceProof{Key: source.key, URL: source.url, SHA256: fmt.Sprintf("%x", sha256.Sum256(b)), Bytes: len(b)})
	}
	return proofs, nil
}

// Source-level assertions are deliberately not a CLI execution or GPU test.
func checkSource(key string, b []byte) error {
	if key == "R" {
		var ref struct {
			Ref    string `json:"ref"`
			Object struct {
				Type string `json:"type"`
				SHA  string `json:"sha"`
			} `json:"object"`
		}
		if json.Unmarshal(b, &ref) != nil || ref.Ref != "refs/tags/v0.32.0" || ref.Object.Type != "commit" || ref.Object.SHA != upstreamRevision {
			return errors.New("engine release revision differs from reviewed pin")
		}
		return nil
	}
	s := string(b)
	if key == "E" && strings.Contains(s, `"VLLM_DISABLE_UVA":`) {
		return errors.New("upstream UVA source assumption changed")
	}
	for _, required := range sourceAssertions[key] {
		if !strings.Contains(s, required) {
			return errors.New("engine source contract differs from reviewed profile")
		}
	}
	return nil
}

var sourceAssertions = map[string][]string{
	"A": {`"--model"`, `"--served-model-name"`, `"--tensor-parallel-size"`, `"--data-parallel-size"`, `"--engram-config"`, `"--attention-config"`, `"--max-model-len"`, `"--max-num-seqs"`, `"--kv-cache-dtype"`, `"--enable-log-requests"`, "action=argparse.BooleanOptionalAction"},
	"F": {"host: str | None", "port: int = 8000", "enable_log_outputs: bool = False", "disable_uvicorn_access_log: bool = False"},
	"E": {`"VLLM_USE_RUST_FRONTEND":`, `"VLLM_ENGINE_READY_TIMEOUT_S":`, `"VLLM_NO_USAGE_STATS":`, `"VLLM_DO_NOT_TRACK":`},
	"C": {"cpu_offload: bool"},
	"D": {"DeepSeek-V4.1-Flash-1017", "deepseek_v41", "FLASHINFER_MLA_SPARSE_DSV41", "indexer_kv_dtype", "mxfp4"},
	"G": {"GLM-5.3", "glm47", "fp8_ds_mla"},
}

func readSource(res *http.Response) ([]byte, error) {
	defer res.Body.Close()
	if res.StatusCode != 200 || res.ContentLength > sourceLimit {
		return nil, errors.New("source metadata status or size rejected")
	}
	b, err := io.ReadAll(io.LimitReader(res.Body, sourceLimit+1))
	if err != nil || len(b) > sourceLimit {
		return nil, errors.New("source metadata read failed or exceeded limit")
	}
	return b, nil
}

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const sourceLimit = 4 << 20

var sourceURLs = []struct{ key, url string }{
	{"A", "https://raw.githubusercontent.com/vllm-project/vllm/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/engine/arg_utils.py"},
	{"F", "https://raw.githubusercontent.com/vllm-project/vllm/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/cli_args.py"},
	{"E", "https://raw.githubusercontent.com/vllm-project/vllm/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/envs.py"},
	{"D", "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml"},
	{"G", "https://raw.githubusercontent.com/vllm-project/recipes/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml"},
	{"T-base", "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/803246156cb54952d0fc93a9fb66cdd4d87af2b3/Dockerfile.b200"},
	{"T-release", "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml"},
	{"T-UVA-patch", "https://raw.githubusercontent.com/tinfoilsh/confidential-deepseek-v4-1-flash/803246156cb54952d0fc93a9fb66cdd4d87af2b3/patches/0001-explicit-uva-fallback.patch"},
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
		// These assertions distinguish a patched Tinfoil release from its
		// upstream base; they do not assert runtime or CC compatibility.
		s := string(b)
		switch source.key {
		case "E":
			if strings.Contains(s, `"VLLM_DISABLE_UVA":`) {
				return proofs, errors.New("upstream UVA source assumption changed")
			}
		case "T-UVA-patch":
			if !strings.Contains(s, `+    "VLLM_DISABLE_UVA":`) {
				return proofs, errors.New("Tinfoil UVA patch assumption changed")
			}
		case "T-base":
			if !strings.Contains(s, upstreamImage) || !strings.Contains(s, "db9527a46873454610df6dbedf79a36d6bf1a7f6") {
				return proofs, errors.New("base engine source pin changed")
			}
		case "T-release":
			if !strings.Contains(s, tinfoilImage) {
				return proofs, errors.New("Tinfoil release source pin changed")
			}
		}
		proofs = append(proofs, sourceProof{Key: source.key, URL: source.url, SHA256: fmt.Sprintf("%x", sha256.Sum256(b)), Bytes: len(b)})
	}
	return proofs, nil
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

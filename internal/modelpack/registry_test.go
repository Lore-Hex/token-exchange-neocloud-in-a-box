// SPDX-License-Identifier: Apache-2.0
package modelpack

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRegistryDigestAndAnonymousAuth(t *testing.T) {
	body := fmt.Sprintf(`{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","config":{"digest":"sha256:%s","size":50},"layers":[{"digest":"sha256:%s","size":100000000000}]}`, testHash, testHash)
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(body)))
	for _, repo := range []string{"vllm/vllm-openai", "ghcr.io/tinfoilsh/confidential-deepseek-v4-1-flash"} {
		t.Run(repo, func(t *testing.T) {
			calls := 0
			r := &remote{client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
				calls++
				switch calls {
				case 1:
					if req.Header.Get("Authorization") != "" {
						t.Fatal("user credentials sent")
					}
					res := response("SECRET_BODY")
					res.StatusCode = 401
					res.Header.Set("WWW-Authenticate", `Bearer realm="https://evil.invalid/SECRET",scope="untrusted"`)
					return res, nil
				case 2:
					if req.URL.Host != "auth.docker.io" && req.URL.Host != "ghcr.io" {
						t.Fatal("followed challenge host")
					}
					if req.Header.Get("Authorization") != "" || !strings.HasSuffix(req.URL.Query().Get("scope"), ":pull") {
						t.Fatal("wrong token request")
					}
					return response(`{"token":"ANONYMOUS_TEST_TOKEN"}`), nil
				case 3:
					if req.Header.Get("Authorization") != "Bearer ANONYMOUS_TEST_TOKEN" || !strings.Contains(req.URL.Path, "/manifests/sha256:") {
						t.Fatal("unexpected manifest request")
					}
					res := response(body)
					res.Header.Set("Docker-Content-Digest", "sha256:"+digest)
					return res, nil
				default:
					t.Fatal("layer/config download or retry")
					return nil, nil
				}
			})}}
			proof, err := verifyImage(context.Background(), repo+"@sha256:"+digest, r)
			if err != nil {
				t.Fatal(err)
			}
			if calls != 3 || proof.ManifestBytes != len(body) || proof.DownloadedBytes > 4096 {
				t.Fatal("bad proof")
			}
		})
	}
}
func TestRegistryFailures(t *testing.T) {
	for _, tc := range []struct {
		name, body, header string
		wrongDigest        bool
	}{
		{"hash", "{}", "", true}, {"header", "{}", "sha256:" + testHash, false}, {"schema", `{"schemaVersion":1}`, "", false}, {"media", `{"schemaVersion":2,"mediaType":"unknown"}`, "", false},
		{"index_empty", `{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[]}`, "", false},
		{"descriptor", `{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[{"digest":"sha256:000","size":-1}]}`, "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			digest := fmt.Sprintf("%x", sha256.Sum256([]byte(tc.body)))
			if tc.wrongDigest {
				digest = testHash
			}
			r := &remote{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
				res := response(tc.body)
				res.Header.Set("Docker-Content-Digest", tc.header)
				return res, nil
			})}}
			if _, err := verifyImage(context.Background(), "vllm/vllm-openai@sha256:"+digest, r); err == nil {
				t.Fatal("invalid registry accepted")
			}
		})
	}
	for _, ref := range []string{"vllm/vllm-openai:latest", "vllm/vllm-openai@sha256:" + strings.Repeat("0", 64), "evil.invalid/repo/image@sha256:" + testHash} {
		if _, err := verifyImage(context.Background(), ref, &remote{}); err == nil {
			t.Fatal("untrusted reference accepted")
		}
	}
}

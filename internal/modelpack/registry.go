// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ImageProof proves registry manifest integrity, not publisher identity, image
// contents, vulnerability status or runtime compatibility. No layers are read.
type ImageProof struct {
	Image           string `json:"image"`
	MediaType       string `json:"media_type"`
	ManifestBytes   int    `json:"manifest_bytes"`
	DownloadedBytes int64  `json:"downloaded_bytes"`
}

// VerifyImage checks only explicitly supported public upstream repositories.
// Registry authentication uses an anonymous, short-lived pull token; no user
// credentials, environment tokens, credential helpers or Docker config are read.
// Protocol: https://distribution.github.io/distribution/spec/api/
// Authentication: https://distribution.github.io/distribution/spec/auth/token/
func VerifyImage(ctx context.Context, image string) (*ImageProof, error) {
	return verifyImage(ctx, image, newRemote())
}

func verifyImage(ctx context.Context, image string, r *remote) (*ImageProof, error) {
	repo, digest, ok := strings.Cut(image, "@sha256:")
	if !ok || !validHash(digest) {
		return nil, errors.New("modelpack: immutable image digest required")
	}
	var host, auth, service, path string
	switch repo {
	case "vllm/vllm-openai":
		host, auth, service, path = "registry-1.docker.io", "https://auth.docker.io/token", "registry.docker.io", repo
	case "ghcr.io/tinfoilsh/confidential-deepseek-v4-1-flash":
		host, auth, service, path = "ghcr.io", "https://ghcr.io/token", "ghcr.io", "tinfoilsh/confidential-deepseek-v4-1-flash"
	default:
		return nil, errors.New("modelpack: unsupported public image repository")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	t := transfer{r: r}
	u, _ := url.Parse("https://" + host + "/v2/" + path + "/manifests/sha256:" + digest)
	h := http.Header{"Accept": []string{"application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"}}
	b, headers, status, err := t.get(ctx, u, maxResponse, h, nil)
	if status == http.StatusUnauthorized {
		// Do not follow an untrusted WWW-Authenticate realm or scope. These
		// allowlisted endpoints grant anonymous pull access to public images.
		a, _ := url.Parse(auth)
		q := url.Values{"service": {service}, "scope": {"repository:" + path + ":pull"}}
		a.RawQuery = q.Encode()
		tokenBody, _, _, tokenErr := t.get(ctx, a, 64<<10, nil, nil)
		if tokenErr != nil {
			return nil, tokenErr
		}
		var token struct {
			Token       string `json:"token"`
			AccessToken string `json:"access_token"`
		}
		if json.Unmarshal(tokenBody, &token) != nil {
			return nil, errors.New("modelpack: invalid anonymous registry response")
		}
		if token.Token == "" {
			token.Token = token.AccessToken
		}
		if token.Token == "" || len(token.Token) > 32768 || strings.ContainsAny(token.Token, "\r\n\x00") {
			return nil, errors.New("modelpack: invalid anonymous registry token")
		}
		h.Set("Authorization", "Bearer "+token.Token)
		b, headers, _, err = t.get(ctx, u, maxResponse, h, nil)
	}
	if err != nil {
		return nil, err
	}
	if fmt.Sprintf("%x", sha256.Sum256(b)) != digest {
		return nil, errors.New("modelpack: registry manifest digest mismatch")
	}
	if got := headers.Get("Docker-Content-Digest"); got != "" && got != "sha256:"+digest {
		return nil, errors.New("modelpack: registry digest header mismatch")
	}
	type descriptor struct {
		Digest string `json:"digest"`
		Size   *int64 `json:"size"`
	}
	var m struct {
		SchemaVersion int          `json:"schemaVersion"`
		MediaType     string       `json:"mediaType"`
		Config        descriptor   `json:"config"`
		Layers        []descriptor `json:"layers"`
		Manifests     []descriptor `json:"manifests"`
	}
	if json.Unmarshal(b, &m) != nil || m.SchemaVersion != 2 {
		return nil, errors.New("modelpack: invalid registry manifest")
	}
	var ds []descriptor
	switch m.MediaType {
	case "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json":
		if len(m.Manifests) == 0 {
			return nil, errors.New("modelpack: empty registry index")
		}
		ds = m.Manifests
	case "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json":
		if len(m.Layers) == 0 {
			return nil, errors.New("modelpack: empty registry layers")
		}
		ds = append(m.Layers, m.Config)
	default:
		return nil, errors.New("modelpack: unsupported registry media type")
	}
	for _, d := range ds {
		if !strings.HasPrefix(d.Digest, "sha256:") || !validHash(strings.TrimPrefix(d.Digest, "sha256:")) || d.Size == nil || *d.Size < 0 {
			return nil, errors.New("modelpack: invalid registry descriptor")
		}
	}
	return &ImageProof{Image: image, MediaType: m.MediaType, ManifestBytes: len(b), DownloadedBytes: t.bytes}, nil
}

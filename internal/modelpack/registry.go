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

// EngineImageProof adds self-declared config consistency to manifest integrity.
// It is not independent source provenance, signing or runtime validation.
type EngineImageProof struct {
	ImageProof
	ConfigDigest string `json:"config_digest"`
	ConfigBytes  int    `json:"config_bytes"`
	Platform     string `json:"platform"`
	Source       string `json:"source"`
	Revision     string `json:"revision"`
}

const maxEngineConfig = 1 << 20

type registryDescriptor struct {
	MediaType string `json:"mediaType"`
	Digest    string `json:"digest"`
	Size      *int64 `json:"size"`
}

type registryManifest struct {
	SchemaVersion int                  `json:"schemaVersion"`
	MediaType     string               `json:"mediaType"`
	Config        registryDescriptor   `json:"config"`
	Layers        []registryDescriptor `json:"layers"`
	Manifests     []registryDescriptor `json:"manifests"`
}

type registryMetadata struct {
	proof    ImageProof
	manifest registryManifest
	baseURL  string
	headers  http.Header
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
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	t := transfer{r: r}
	m, err := readRegistryManifest(ctx, image, &t)
	if err != nil {
		return nil, err
	}
	return &m.proof, nil
}

// VerifyEngineImage verifies a pinned, single-platform vLLM manifest and its
// bounded config blob. Only anonymous registry auth is used; no layers are read.
// One exact-digest Docker CDN redirect is allowed without registry credentials.
// The config fields are assertions, not provenance.
func VerifyEngineImage(ctx context.Context, image, revision string) (*EngineImageProof, error) {
	return verifyEngineImage(ctx, image, revision, newRemote())
}

func verifyEngineImage(ctx context.Context, image, revision string, r *remote) (*EngineImageProof, error) {
	if !validCommit(revision) || !strings.HasPrefix(image, "vllm/vllm-openai@sha256:") {
		return nil, errors.New("modelpack: pinned vLLM image and immutable revision required")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	t := transfer{r: r}
	m, err := readRegistryManifest(ctx, image, &t)
	if err != nil {
		return nil, err
	}
	configType := ""
	switch m.manifest.MediaType {
	case "application/vnd.oci.image.manifest.v1+json":
		configType = "application/vnd.oci.image.config.v1+json"
	case "application/vnd.docker.distribution.manifest.v2+json":
		configType = "application/vnd.docker.container.image.v1+json"
	default:
		return nil, errors.New("modelpack: single-platform engine manifest required")
	}
	d := m.manifest.Config
	if len(m.manifest.Manifests) != 0 || d.MediaType != configType || d.Size == nil || *d.Size <= 0 || *d.Size > maxEngineConfig {
		return nil, errors.New("modelpack: invalid engine config descriptor")
	}
	u, _ := url.Parse(m.baseURL + "/blobs/" + d.Digest)
	m.headers.Set("Accept", configType)
	b, headers, status, err := t.get(ctx, u, *d.Size, m.headers, nil)
	if err != nil && status == http.StatusTemporaryRedirect {
		// Docker's documented CDN uses a signed query. Keep it opaque, bounded
		// and in memory, and never forward the anonymous registry pull token.
		cdn, parseErr := url.Parse(headers.Get("Location"))
		if parseErr != nil || !engineConfigCDN(cdn, d.Digest) {
			return nil, errors.New("modelpack: engine config CDN rejected")
		}
		b, headers, _, err = t.get(ctx, cdn, *d.Size, nil, nil)
	}
	if err != nil {
		return nil, err
	}
	if int64(len(b)) != *d.Size || "sha256:"+fmt.Sprintf("%x", sha256.Sum256(b)) != d.Digest {
		return nil, errors.New("modelpack: engine config size or digest mismatch")
	}
	if got := headers.Get("Docker-Content-Digest"); got != "" && got != d.Digest {
		return nil, errors.New("modelpack: engine config digest header mismatch")
	}
	var c struct {
		OS           string `json:"os"`
		Architecture string `json:"architecture"`
		Config       struct {
			Labels     map[string]string `json:"Labels"`
			Env        []string          `json:"Env"`
			Entrypoint []string          `json:"Entrypoint"`
		} `json:"config"`
	}
	if json.Unmarshal(b, &c) != nil || c.OS != "linux" || c.Architecture != "amd64" {
		return nil, errors.New("modelpack: linux/amd64 engine config required")
	}
	const source = "https://github.com/vllm-project/vllm"
	if c.Config.Labels["org.opencontainers.image.source"] != source || c.Config.Labels["org.opencontainers.image.revision"] != revision {
		return nil, errors.New("modelpack: engine source or revision label mismatch")
	}
	buildCommits := 0
	for _, env := range c.Config.Env {
		key, value, ok := strings.Cut(env, "=")
		if key == "VLLM_BUILD_COMMIT" {
			if !ok || value != revision {
				return nil, errors.New("modelpack: engine build revision mismatch")
			}
			buildCommits++
		}
	}
	if buildCommits != 1 {
		return nil, errors.New("modelpack: exactly one engine build revision required")
	}
	if len(c.Config.Entrypoint) != 2 || c.Config.Entrypoint[0] != "vllm" || c.Config.Entrypoint[1] != "serve" {
		return nil, errors.New("modelpack: vllm serve entrypoint required")
	}
	m.proof.DownloadedBytes = t.bytes
	return &EngineImageProof{ImageProof: m.proof, ConfigDigest: d.Digest, ConfigBytes: len(b), Platform: "linux/amd64", Source: source, Revision: revision}, nil
}

// Docker documents this host for pull delivery; the path was observed for our
// pinned config blob. No wildcard CDN hosts, alternate blobs or onward redirects.
// https://docs.docker.com/desktop/enterprise/allow-list/
func engineConfigCDN(u *url.URL, digest string) bool {
	hash := strings.TrimPrefix(digest, "sha256:")
	if !strings.HasPrefix(digest, "sha256:") || !validHash(hash) || u == nil {
		return false
	}
	path := "/registry-v2/docker/registry/v2/blobs/sha256/" + hash[:2] + "/" + hash + "/data"
	return u.Scheme == "https" && u.Host == "production.cloudfront.docker.com" &&
		u.User == nil && u.Fragment == "" && u.Opaque == "" && u.Path == path &&
		u.EscapedPath() == path && len(u.RawQuery) <= 8192
}

func readRegistryManifest(ctx context.Context, image string, t *transfer) (*registryMetadata, error) {
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
	baseURL := "https://" + host + "/v2/" + path
	u, _ := url.Parse(baseURL + "/manifests/sha256:" + digest)
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
	var m registryManifest
	if json.Unmarshal(b, &m) != nil || m.SchemaVersion != 2 {
		return nil, errors.New("modelpack: invalid registry manifest")
	}
	var ds []registryDescriptor
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
	return &registryMetadata{proof: ImageProof{Image: image, MediaType: m.MediaType, ManifestBytes: len(b), DownloadedBytes: t.bytes}, manifest: m, baseURL: baseURL, headers: h}, nil
}

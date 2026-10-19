// SPDX-License-Identifier: Apache-2.0
package modelpack

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
	"time"
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

func engineConfigFixture() map[string]any {
	return map[string]any{
		"os": "linux", "architecture": "amd64",
		"config": map[string]any{
			"Labels": map[string]string{"org.opencontainers.image.source": "https://github.com/vllm-project/vllm", "org.opencontainers.image.revision": testRev},
			"Env":    []string{"PATH=/usr/bin", "VLLM_BUILD_COMMIT=" + testRev}, "Entrypoint": []string{"vllm", "serve"},
		},
	}
}

func engineManifestFixture(config string, oci bool) registryManifest {
	manifestType, configType := "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.docker.container.image.v1+json"
	if oci {
		manifestType, configType = "application/vnd.oci.image.manifest.v1+json", "application/vnd.oci.image.config.v1+json"
	}
	return registryManifest{SchemaVersion: 2, MediaType: manifestType,
		Config: registryDescriptor{MediaType: configType, Digest: "sha256:" + fmt.Sprintf("%x", sha256.Sum256([]byte(config))), Size: ptr(int64(len(config)))},
		Layers: []registryDescriptor{{Digest: "sha256:" + testHash, Size: ptr(100000000000)}},
	}
}

// Only the exact manifest and config are reachable; layer requests fail the test.
func engineRegistry(t *testing.T, m registryManifest, config func(*http.Request) (*http.Response, error)) (string, *remote, *int) {
	t.Helper()
	body := jsonText(m)
	image := "vllm/vllm-openai@sha256:" + fmt.Sprintf("%x", sha256.Sum256([]byte(body)))
	calls := 0
	r := &remote{client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		calls++
		if req.URL.Scheme != "https" || req.URL.Host != "registry-1.docker.io" || req.URL.RawQuery != "" || req.Header.Get("Authorization") != "" || req.Header.Get("Cookie") != "" {
			t.Fatal("unexpected destination or credentials")
		}
		switch req.URL.Path {
		case "/v2/vllm/vllm-openai/manifests/" + strings.Split(image, "@")[1]:
			return response(body), nil
		case "/v2/vllm/vllm-openai/blobs/" + m.Config.Digest:
			return config(req)
		default:
			t.Fatal("unexpected request, possibly a layer")
			return nil, errors.New("unexpected request")
		}
	})}}
	return image, r, &calls
}

func TestEngineImageConfigConsistency(t *testing.T) {
	for _, oci := range []bool{false, true} {
		t.Run(fmt.Sprint(oci), func(t *testing.T) {
			b := jsonText(engineConfigFixture())
			m := engineManifestFixture(b, oci)
			image, r, calls := engineRegistry(t, m, func(req *http.Request) (*http.Response, error) {
				deadline, ok := req.Context().Deadline()
				if !ok || time.Until(deadline) > 2*time.Minute || req.Header.Get("Accept") != m.Config.MediaType {
					t.Fatal("missing deadline or wrong config accept type")
				}
				res := response(b)
				res.Header.Set("Docker-Content-Digest", m.Config.Digest)
				return res, nil
			})
			jar, _ := cookiejar.New(nil)
			u, _ := url.Parse("https://registry-1.docker.io")
			jar.SetCookies(u, []*http.Cookie{{Name: "credential", Value: "SECRET"}})
			r.client.Jar = jar
			t.Setenv("DOCKER_AUTH_CONFIG", `{"auths":{"registry-1.docker.io":{"auth":"SECRET"}}}`)
			p, err := verifyEngineImage(context.Background(), image, testRev, r)
			if err != nil || p == nil {
				t.Fatalf("valid config rejected: %v", err)
			}
			if *calls != 2 || p.Image != image || p.ConfigDigest != m.Config.Digest || p.ConfigBytes != len(b) || p.Platform != "linux/amd64" || p.Revision != testRev || p.Source != "https://github.com/vllm-project/vllm" || p.DownloadedBytes != int64(len(jsonText(m))+len(b)) {
				t.Fatal("incorrect config proof or transfer accounting")
			}
		})
	}
}

func TestEngineImageAnonymousConfigAuth(t *testing.T) {
	for _, redirect := range []bool{false, true} {
		t.Run(fmt.Sprint(redirect), func(t *testing.T) {
			b := jsonText(engineConfigFixture())
			m := engineManifestFixture(b, false)
			body := jsonText(m)
			image := "vllm/vllm-openai@sha256:" + fmt.Sprintf("%x", sha256.Sum256([]byte(body)))
			calls := 0
			r := &remote{client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
				calls++
				if req.Header.Get("Cookie") != "" {
					t.Fatal("cookie sent")
				}
				switch calls {
				case 1:
					if req.Header.Get("Authorization") != "" {
						t.Fatal("ambient credentials sent")
					}
					res := response("SECRET_BODY")
					res.StatusCode = 401
					res.Header.Set("WWW-Authenticate", `Bearer realm="https://evil.invalid/SECRET"`)
					return res, nil
				case 2:
					if req.URL.Host != "auth.docker.io" || req.URL.Path != "/token" || req.URL.Query().Get("scope") != "repository:vllm/vllm-openai:pull" || req.URL.Query().Get("service") != "registry.docker.io" || req.Header.Get("Authorization") != "" {
						t.Fatal("untrusted token request")
					}
					return response(`{"token":"ANONYMOUS_TEST_TOKEN"}`), nil
				case 3, 4:
					if req.URL.Host != "registry-1.docker.io" || req.Header.Get("Authorization") != "Bearer ANONYMOUS_TEST_TOKEN" {
						t.Fatal("auth escaped registry")
					}
					if calls == 3 && req.URL.Path == "/v2/vllm/vllm-openai/manifests/"+strings.Split(image, "@")[1] {
						return response(body), nil
					}
					if calls == 4 && req.URL.Path == "/v2/vllm/vllm-openai/blobs/"+m.Config.Digest {
						res := response(b)
						if redirect {
							res.StatusCode = http.StatusTemporaryRedirect
							res.Header.Set("Location", "https://cdn.invalid/SECRET?token=SECRET")
						}
						return res, nil
					}
				}
				t.Fatal("unexpected request, possibly layer/redirect")
				return nil, errors.New("unexpected request")
			})}}
			p, err := verifyEngineImage(context.Background(), image, testRev, r)
			if redirect {
				if err == nil || p != nil || calls != 4 || strings.Contains(err.Error(), "SECRET") {
					t.Fatal("authenticated redirect accepted or leaked")
				}
				return
			}
			if err != nil || calls != 4 || p.DownloadedBytes != int64(len(body)+len(b)+len(`{"token":"ANONYMOUS_TEST_TOKEN"}`)) {
				t.Fatalf("anonymous config fetch failed: %v", err)
			}
		})
	}
}

func TestEngineImageRejectsConfigClaims(t *testing.T) {
	for name, mutate := range map[string]func(map[string]any){
		"os":               func(c map[string]any) { c["os"] = "windows" },
		"arch":             func(c map[string]any) { c["architecture"] = "arm64" },
		"missing_platform": func(c map[string]any) { delete(c, "architecture") },
		"missing_config":   func(c map[string]any) { delete(c, "config") },
		"source": func(c map[string]any) {
			c["config"].(map[string]any)["Labels"].(map[string]string)["org.opencontainers.image.source"] = "https://evil.invalid/SECRET"
		},
		"revision": func(c map[string]any) {
			c["config"].(map[string]any)["Labels"].(map[string]string)["org.opencontainers.image.revision"] = strings.Repeat("2", 40)
		},
		"missing_labels": func(c map[string]any) { delete(c["config"].(map[string]any), "Labels") },
		"missing_env":    func(c map[string]any) { delete(c["config"].(map[string]any), "Env") },
		"env_revision":   func(c map[string]any) { c["config"].(map[string]any)["Env"] = []string{"VLLM_BUILD_COMMIT=SECRET"} },
		"env_bare":       func(c map[string]any) { c["config"].(map[string]any)["Env"] = []string{"VLLM_BUILD_COMMIT"} },
		"env_duplicate": func(c map[string]any) {
			c["config"].(map[string]any)["Env"] = []string{"VLLM_BUILD_COMMIT=" + testRev, "VLLM_BUILD_COMMIT=" + testRev}
		},
		"entrypoint": func(c map[string]any) {
			c["config"].(map[string]any)["Entrypoint"] = []string{"sh", "-c", "vllm serve"}
		},
		"entrypoint_extra": func(c map[string]any) {
			c["config"].(map[string]any)["Entrypoint"] = []string{"vllm", "serve", "SECRET"}
		},
		"entrypoint_empty": func(c map[string]any) { c["config"].(map[string]any)["Entrypoint"] = []string{} },
	} {
		t.Run(name, func(t *testing.T) {
			c := engineConfigFixture()
			mutate(c)
			b := jsonText(c)
			image, r, _ := engineRegistry(t, engineManifestFixture(b, false), func(*http.Request) (*http.Response, error) { return response(b), nil })
			p, err := verifyEngineImage(context.Background(), image, testRev, r)
			if err == nil || p != nil || strings.Contains(err.Error(), "SECRET") {
				t.Fatal("invalid config accepted or leaked")
			}
		})
	}
}

func TestEngineImageRejectsDescriptorAndBlob(t *testing.T) {
	for _, name := range []string{"hash", "short", "long", "zero", "oversized", "media", "index", "index_and_config", "json", "header", "read", "status", "content_length"} {
		t.Run(name, func(t *testing.T) {
			b := jsonText(engineConfigFixture())
			if name == "json" {
				b = "SECRET_INVALID_JSON"
			}
			m := engineManifestFixture(b, false)
			wantCalls := 2
			switch name {
			case "hash":
				m.Config.Digest = "sha256:" + testHash
			case "short":
				m.Config.Size = ptr(int64(len(b) + 1))
			case "long":
				m.Config.Size = ptr(int64(len(b) - 1))
			case "zero":
				m.Config.Size = ptr(0)
				wantCalls = 1
			case "oversized":
				m.Config.Size = ptr(maxEngineConfig + 1)
				wantCalls = 1
			case "media":
				m.Config.MediaType = "application/octet-stream"
				wantCalls = 1
			case "index", "index_and_config":
				m.Manifests = []registryDescriptor{{Digest: "sha256:" + testHash, Size: ptr(100)}}
				if name == "index" {
					m.MediaType = "application/vnd.oci.image.index.v1+json"
				}
				wantCalls = 1
			}
			image, r, calls := engineRegistry(t, m, func(*http.Request) (*http.Response, error) {
				res := response(b)
				switch name {
				case "header":
					res.Header.Set("Docker-Content-Digest", "sha256:"+testHash)
				case "read":
					res.Body = io.NopCloser(registryErrorReader{})
				case "status":
					res.StatusCode = 403
				case "content_length":
					res.ContentLength = *m.Config.Size + 1
				}
				return res, nil
			})
			p, err := verifyEngineImage(context.Background(), image, testRev, r)
			if err == nil || p != nil || *calls != wantCalls || strings.Contains(err.Error(), "SECRET") {
				t.Fatalf("invalid metadata accepted, leaked or fetched: calls=%d, err=%v", *calls, err)
			}
		})
	}
}

type registryErrorReader struct{}

func (registryErrorReader) Read([]byte) (int, error) { return 0, errors.New("SECRET_READ_ERROR") }

func TestEngineImageRejectsRedirectsAndCancellation(t *testing.T) {
	for _, target := range []string{"https://registry-1.docker.io/v2/vllm/vllm-openai/blobs/other", "https://cdn.invalid/SECRET?token=SECRET", "http://registry-1.docker.io/blob", "https://user:SECRET@registry-1.docker.io/blob"} {
		t.Run(target, func(t *testing.T) {
			b := jsonText(engineConfigFixture())
			image, r, calls := engineRegistry(t, engineManifestFixture(b, false), func(*http.Request) (*http.Response, error) {
				res := response("SECRET_BODY")
				res.StatusCode = http.StatusTemporaryRedirect
				res.Header.Set("Location", target)
				return res, nil
			})
			if p, err := verifyEngineImage(context.Background(), image, testRev, r); err == nil || p != nil || strings.Contains(err.Error(), "SECRET") || *calls != 2 {
				t.Fatal("redirect accepted, credentials forwarded or diagnostic leaked")
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := &remote{client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		return nil, req.Context().Err()
	})}}
	if _, err := verifyEngineImage(ctx, "vllm/vllm-openai@sha256:"+testHash, testRev, r); !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation not preserved")
	}
	b := jsonText(engineConfigFixture())
	image, r, _ := engineRegistry(t, engineManifestFixture(b, false), func(req *http.Request) (*http.Response, error) {
		<-req.Context().Done()
		return nil, req.Context().Err()
	})
	ctx, cancel = context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if p, err := verifyEngineImage(ctx, image, testRev, r); p != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("config timeout not preserved")
	}
}

func TestEngineImageInvalidInputsAndLegacyIndex(t *testing.T) {
	r := &remote{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("invalid input reached network"); return nil, nil })}}
	for _, tc := range [][2]string{
		{"vllm/vllm-openai:latest", testRev}, {"vllm/vllm-openai@sha256:" + testHash, "main"},
		{"vllm/vllm-openai@sha256:" + testHash, strings.Repeat("0", 40)},
		{"vllm/vllm-openai@sha256:" + strings.Repeat("0", 64), testRev},
		{"ghcr.io/tinfoilsh/confidential-deepseek-v4-1-flash@sha256:" + testHash, testRev},
	} {
		if _, err := verifyEngineImage(context.Background(), tc[0], tc[1], r); err == nil {
			t.Fatal("invalid input accepted")
		}
	}
	b := jsonText(engineConfigFixture())
	m := engineManifestFixture(b, true)
	m.MediaType = "application/vnd.oci.image.index.v1+json"
	m.Manifests = []registryDescriptor{{Digest: "sha256:" + testHash, Size: ptr(100)}}
	image, r, calls := engineRegistry(t, m, func(*http.Request) (*http.Response, error) { t.Fatal("legacy API fetched config"); return nil, nil })
	p, err := verifyImage(context.Background(), image, r)
	if err != nil || *calls != 1 {
		t.Fatal("legacy index behavior changed")
	}
	if strings.Contains(jsonText(p), "config_") || strings.Contains(jsonText(p), "revision") || strings.Contains(jsonText(p), "platform") {
		t.Fatal("legacy proof acquired unverified config fields")
	}
}

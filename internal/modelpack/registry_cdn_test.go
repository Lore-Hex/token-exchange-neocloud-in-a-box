// SPDX-License-Identifier: Apache-2.0
package modelpack

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
)

func engineCDNURL(digest string) string {
	hash := strings.TrimPrefix(digest, "sha256:")
	return "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/" + hash[:2] + "/" + hash + "/data"
}

func TestEngineConfigCDNPolicy(t *testing.T) {
	digest := "sha256:" + testHash
	good := engineCDNURL(digest)
	for _, target := range []string{good, good + "?opaque=SIGNED_VALUE"} {
		u, _ := url.Parse(target)
		if !engineConfigCDN(u, digest) {
			t.Fatal("exact digest CDN rejected")
		}
	}
	for _, target := range []string{
		strings.Replace(good, "https:", "http:", 1),
		strings.Replace(good, "production.cloudfront.docker.com", "production.cloudfront.docker.com.evil.invalid", 1),
		strings.Replace(good, "production.cloudfront.docker.com", "production.cloudfront.docker.com:443", 1),
		strings.Replace(good, "https://", "https://user:SECRET@", 1),
		strings.Replace(good, testHash, strings.Repeat("a", 64), 1),
		strings.Replace(good, "/registry-v2/", "/%72egistry-v2/", 1),
		good + "/other", good + "#SECRET", good + "?" + strings.Repeat("a", 8193),
		good + "/../data", "/relative", "https:opaque",
	} {
		u, err := url.Parse(target)
		if err == nil && engineConfigCDN(u, digest) {
			t.Fatal("unsafe CDN URL accepted")
		}
	}
	if engineConfigCDN(nil, digest) || engineConfigCDN(&url.URL{}, "sha256:short") {
		t.Fatal("invalid digest or URL accepted")
	}
}

func TestEngineConfigCDNStripsAuthAndBoundsResponse(t *testing.T) {
	for _, mode := range []string{"pass", "onward", "wrong_hash", "oversized", "oversized_declared", "http_error"} {
		t.Run(mode, func(t *testing.T) {
			b := jsonText(engineConfigFixture())
			m := engineManifestFixture(b, false)
			body := jsonText(m)
			image := "vllm/vllm-openai@sha256:" + fmt.Sprintf("%x", sha256.Sum256([]byte(body)))
			cdnURL := engineCDNURL(m.Config.Digest) + "?opaque=SECRET_SIGNED_QUERY"
			jar, _ := cookiejar.New(nil)
			u, _ := url.Parse(cdnURL)
			jar.SetCookies(u, []*http.Cookie{{Name: "secret", Value: "SECRET_COOKIE"}})
			calls := 0
			redirectBody := &countingReader{remaining: 1 << 20}
			var oversizedBody *countingReader
			r := &remote{client: &http.Client{Jar: jar, Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
				calls++
				switch calls {
				case 1:
					res := response("")
					res.StatusCode = 401
					return res, nil
				case 2:
					return response(`{"token":"ANONYMOUS_TEST_TOKEN"}`), nil
				case 3:
					return response(body), nil
				case 4:
					if req.URL.Host != "registry-1.docker.io" || req.Header.Get("Authorization") != "Bearer ANONYMOUS_TEST_TOKEN" || req.URL.Path != "/v2/vllm/vllm-openai/blobs/"+m.Config.Digest {
						t.Fatal("wrong authenticated config request")
					}
					res := response("")
					res.StatusCode = 307
					res.Header.Set("Location", cdnURL)
					res.Body = io.NopCloser(redirectBody)
					return res, nil
				case 5:
					if req.URL.String() != cdnURL || req.Header.Get("Authorization") != "" || req.Header.Get("Cookie") != "" {
						t.Fatal("registry auth/cookie forwarded or different blob fetched")
					}
					res := response(b)
					switch mode {
					case "onward":
						res.StatusCode = 307
						res.Header.Set("Location", cdnURL)
					case "wrong_hash":
						res.Body = io.NopCloser(strings.NewReader(strings.Repeat("x", len(b))))
					case "oversized", "oversized_declared":
						oversizedBody = &countingReader{remaining: int64(len(b) + 10000)}
						res.Body = io.NopCloser(oversizedBody)
						res.ContentLength = -1
						if mode == "oversized_declared" {
							res.ContentLength = int64(len(b) + 10000)
						}
					case "http_error":
						res.StatusCode = 403
					}
					return res, nil
				default:
					t.Fatal("extra request or onward redirect")
					return nil, nil
				}
			})}}
			proof, err := verifyEngineImage(context.Background(), image, testRev, r)
			if (mode == "pass") != (err == nil) || calls != 5 || redirectBody.read != 0 {
				t.Fatalf("result: calls=%d err=%v", calls, err)
			}
			if mode == "pass" && (proof == nil || proof.ConfigDigest != m.Config.Digest) {
				t.Fatal("missing digest proof")
			}
			if err != nil && strings.Contains(err.Error(), "SECRET") {
				t.Fatal("signed URL leaked")
			}
			if oversizedBody != nil && (oversizedBody.read > int64(len(b)+1) || (mode == "oversized_declared" && oversizedBody.read != 0)) {
				t.Fatal("CDN body exceeds budget")
			}
		})
	}
}

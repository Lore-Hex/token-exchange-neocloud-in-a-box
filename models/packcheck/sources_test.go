// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

type testTransport func(*http.Request) (*http.Response, error)

func (f testTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type sourceReader struct{ left, read int }

func (r *sourceReader) Read(p []byte) (int, error) {
	if r.left == 0 {
		return 0, io.EOF
	}
	n := min(r.left, len(p))
	clear(p[:n])
	r.left -= n
	r.read += n
	return n, nil
}

func TestSourceResponseBounds(t *testing.T) {
	for _, known := range []bool{true, false} {
		r := &sourceReader{left: sourceLimit + 100}
		res := &http.Response{StatusCode: 200, ContentLength: -1, Body: io.NopCloser(r)}
		if known {
			res.ContentLength = int64(r.left)
		}
		if _, err := readSource(res); err == nil {
			t.Fatal("oversize source accepted")
		}
		if r.read > sourceLimit+1 || (known && r.read != 0) {
			t.Fatal("source overread")
		}
	}
	r := &sourceReader{left: 1000}
	if _, err := readSource(&http.Response{StatusCode: 302, Body: io.NopCloser(r)}); err == nil || r.read != 0 {
		t.Fatal("redirect source body read")
	}
}

func TestSourceAuditFixedURLsAndNoSecrets(t *testing.T) {
	calls := 0
	client := sourceClient()
	client.Transport = testTransport(func(req *http.Request) (*http.Response, error) {
		if req.URL.String() != sourceURLs[calls].url || req.URL.Scheme != "https" || req.URL.Host != "raw.githubusercontent.com" || req.Header.Get("Authorization") != "" || req.Header.Get("Cookie") != "" {
			t.Fatal("unsafe source request")
		}
		key := sourceURLs[calls].key
		calls++
		body := "test source"
		switch key {
		case "T-base":
			body = upstreamImage + " db9527a46873454610df6dbedf79a36d6bf1a7f6"
		case "T-release":
			body = tinfoilImage
		case "T-UVA-patch":
			body = `+    "VLLM_DISABLE_UVA":`
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), ContentLength: int64(len(body))}, nil
	})
	proofs, err := auditSources(context.Background(), client)
	if err != nil || len(proofs) != 8 || calls != 8 {
		t.Fatalf("audit: %v", err)
	}
	client.Transport = testTransport(func(*http.Request) (*http.Response, error) { return nil, errors.New("SECRET_URL") })
	if _, err := auditSources(context.Background(), client); err == nil || strings.Contains(err.Error(), "SECRET") {
		t.Fatal("source error leaked")
	}
	if err := client.CheckRedirect(nil, nil); err != http.ErrUseLastResponse {
		t.Fatal("source redirects enabled")
	}
}

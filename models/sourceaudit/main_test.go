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

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestFetchBoundaries(t *testing.T) {
	for _, name := range []string{"ok", "status", "link", "large known", "large chunked", "transport", "redirect"} {
		t.Run(name, func(t *testing.T) {
			original := http.DefaultTransport
			t.Cleanup(func() { http.DefaultTransport = original })
			http.DefaultTransport = roundTrip(func(r *http.Request) (*http.Response, error) {
				if r.URL.Host != "huggingface.co" {
					t.Fatal("followed untrusted redirect")
				}
				if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
					t.Fatal("sent credentials")
				}
				resp := &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("small")), ContentLength: -1, Request: r}
				switch name {
				case "status":
					resp.StatusCode = 403
					resp.Body = io.NopCloser(strings.NewReader("SECRET_BODY"))
				case "link":
					resp.Header.Set("Link", "<https://example.invalid/SECRET_PATH?key=SECRET_QUERY>; rel=next")
				case "large known":
					resp.ContentLength = responseLimit + 1
				case "large chunked":
					resp.Body = io.NopCloser(strings.NewReader(strings.Repeat("x", responseLimit+1)))
				case "transport":
					return nil, errors.New("SECRET_TRANSPORT")
				case "redirect":
					resp.StatusCode = 302
					resp.Header.Set("Location", "https://example.invalid/SECRET_PATH?token=SECRET_QUERY")
				}
				return resp, nil
			})
			b, s, err := fetch(context.Background(), "fixture", "https://huggingface.co/api/models/fixture/model")
			if name == "ok" {
				if err != nil || string(b) != "small" || s.Bytes != 5 || len(s.SHA256) != 64 {
					t.Fatalf("valid source rejected: %v", err)
				}
			} else if err == nil || strings.Contains(err.Error(), "SECRET") || strings.Contains(err.Error(), "https://") {
				t.Fatalf("unsafe or absent error: %v", err)
			}
		})
	}
	for _, u := range []string{"http://huggingface.co/file", "https://huggingface.co.evil.invalid/file", "https://secret@huggingface.co/file", "https://huggingface.co/file#fragment"} {
		if _, _, err := fetch(context.Background(), "fixture", u); err == nil {
			t.Fatal("accepted unsafe source URL")
		}
	}
}

func TestSummarize(t *testing.T) {
	m := model{PlannedBytes: 12}
	body := `[{"type":"directory","path":"nested"},{"type":"file","path":"nested/config.json","size":3},{"type":"file","path":"model.safetensors","size":12,"lfs":{"oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":12}}]`
	if err := summarize([]byte(body), &m); err != nil {
		t.Fatal(err)
	}
	if m.FileCount != 2 || m.WeightCount != 1 || m.TotalBytes != 15 || m.WeightBytes != 12 || m.OtherBytes != 3 || m.PlanMatchesFiles || !m.AllLFSHasSHA256 {
		t.Fatalf("incorrect accounting: %+v", m)
	}
	for _, body := range []string{
		`null`, `[]`, `{}`, `[{"type":"directory","path":"empty"}]`,
		`[{"type":"file","path":"a"}]`, `[{"type":"file","path":"a","size":-1}]`,
		`[{"type":"file","path":"a","size":1},{"type":"file","path":"a","size":1}]`,
		`[{"type":"symlink","path":"a","size":1}]`, `[{"type":"file","size":1}]`,
		`[{"type":"file","path":"a","size":9223372036854775807},{"type":"file","path":"b","size":1}]`,
		`[{"type":"file","path":"a","size":1,"lfs":{"oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":2}}]`,
		`[{"type":"file","path":"a","size":1,"lfs":{"oid":"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!","size":1}}]`,
		`[{"type":"file","path":"a","size":1,"lfs":{"oid":"0000000000000000000000000000000000000000000000000000000000000000","size":1}}]`,
	} {
		if err := summarize([]byte(body), &model{}); err == nil {
			t.Fatal("accepted invalid metadata")
		}
	}
}

func TestNoOverwrite(t *testing.T) {
	if err := run(t.TempDir()); err == nil {
		t.Fatal("existing evidence directory was accepted")
	}
}

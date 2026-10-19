// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

func TestWeightRedirectPolicy(t *testing.T) {
	original, _ := url.Parse("https://huggingface.co/org/model/resolve/" + testRev + "/sub/config.json")
	cache := "https://huggingface.co/api/resolve-cache/models/org/model/" + testRev + "/sub/config.json"
	for _, good := range []string{original.String(), cache, strings.Replace(cache, "sub/config", "sub%2Fconfig", 1), "https://us.aws.cdn.hf.co/xet-bridge-us/object?signature=PRIVATE", "https://us.gcp.cdn.hf.co/object", "https://cdn-lfs-us-1.hf.co/object", "https://cdn-lfs-eu-1.hf.co/object"} {
		u, _ := url.Parse(good)
		if !allowedWeightURL(u, original) {
			t.Fatal("documented destination rejected")
		}
	}
	for _, bad := range []string{"http://us.aws.cdn.hf.co/object", "https://us.aws.cdn.hf.co:444/object", "https://us.aws.cdn.hf.co.evil.invalid/object", "https://PRIVATE@us.aws.cdn.hf.co/object", "https://127.0.0.1/object", "https://169.254.169.254/latest", "https://huggingface.co/other", strings.Replace(cache, testRev, "main", 1), cache + "#PRIVATE", strings.Replace(cache, "/org/", "/%6frg/", 1), "https://us.aws.cdn.hf.co/object?" + strings.Repeat("a", 16384)} {
		u, _ := url.Parse(bad)
		if allowedWeightURL(u, original) {
			t.Fatal("unsafe redirect accepted")
		}
	}
}

func TestWeightDownloadRedirectsDoNotReadBodiesOrForwardCredentials(t *testing.T) {
	pack, _, bodies, _ := stageFixture(t)
	in, err := loadStage(pack)
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	redirectBody := &countingReader{remaining: 1 << 20}
	d := &weightDownload{client: &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("Referer") != "" {
			t.Fatal("credentials forwarded")
		}
		if calls == 1 {
			res := response("")
			res.StatusCode = 302
			res.Body = io.NopCloser(redirectBody)
			res.Header.Set("Location", "https://us.aws.cdn.hf.co/object?signature=PRIVATE")
			res.Header.Set("Set-Cookie", "PRIVATE=secret")
			return res, nil
		}
		return response(bodies["config.json"]), nil
	})}}
	body, err := d.open(context.Background(), in.pack, in.files[0])
	if err != nil {
		t.Fatal(err)
	}
	defer body.Close()
	b, err := io.ReadAll(body)
	if err != nil || string(b) != bodies["config.json"] || calls != 2 || redirectBody.read != 0 {
		t.Fatal("redirect body read or incorrect download")
	}
}

func TestWeightHTTPFailureBounds(t *testing.T) {
	for _, kind := range []string{"redirect-loop", "redirect-private", "http-error", "network", "partial-response", "encoding", "large-length"} {
		t.Run(kind, func(t *testing.T) {
			pack, _, _, _ := stageFixture(t)
			in, err := loadStage(pack)
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			data := &countingReader{remaining: 1 << 20}
			d := &weightDownload{client: &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				if kind == "network" {
					return nil, errors.New("PRIVATE signed query")
				}
				res := response("")
				res.Body = io.NopCloser(data)
				switch kind {
				case "redirect-loop":
					res.StatusCode = 302
					res.Header.Set("Location", r.URL.String())
				case "redirect-private":
					res.StatusCode = 307
					res.Header.Set("Location", "https://169.254.169.254/PRIVATE")
				case "http-error":
					res.StatusCode = 503
				case "partial-response":
					res.StatusCode = 206
				case "encoding":
					res.Header.Set("Content-Encoding", "gzip")
				case "large-length":
					res.ContentLength = 1 << 20
				}
				return res, nil
			})}}
			body, err := d.open(context.Background(), in.pack, in.files[0])
			if body != nil || err == nil || strings.Contains(err.Error(), "PRIVATE") || data.read != 0 || calls > 5 {
				t.Fatal("HTTP failure escaped limits or leaked")
			}
		})
	}
}

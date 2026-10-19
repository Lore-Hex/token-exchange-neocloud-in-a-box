// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

type weightDownload struct{ client *http.Client }

func newWeightDownload() *weightDownload {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	t.DisableCompression = true
	t.MaxResponseHeaderBytes = 64 << 10
	t.ResponseHeaderTimeout = 60 * time.Second
	return &weightDownload{client: &http.Client{Transport: t, Timeout: time.Hour,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

// Fixed public endpoints from HF's download documentation, reviewed 2026-10-19:
// https://huggingface.co/docs/hub/datasets-downloading#network-requirements
// No wildcard, runtime endpoint overrides, credentials, cookies or ambient proxy.
func allowedWeightURL(u, original *url.URL) bool {
	if u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Opaque != "" || len(u.String()) > 16384 {
		return false
	}
	if u.Host == "huggingface.co" {
		// Resolve-cache drops the /resolve/ segment. Preserve the pinned repo,
		// commit and exact file, including HF's encoded nested-filename form.
		repo, suffix, ok := strings.Cut(original.Path, "/resolve/")
		if !ok {
			return false
		}
		revision, file, ok := strings.Cut(suffix, "/")
		if !ok {
			return false
		}
		cache := "/api/resolve-cache/models" + repo + "/" + suffix
		encoded := "/api/resolve-cache/models" + repo + "/" + revision + "/" + url.PathEscape(file)
		return (u.Path == original.Path && u.EscapedPath() == original.EscapedPath()) ||
			(u.Path == cache && (u.EscapedPath() == cache || u.EscapedPath() == encoded))
	}
	switch u.Host {
	case "us.aws.cdn.hf.co", "us.gcp.cdn.hf.co", "cdn-lfs-us-1.hf.co", "cdn-lfs-eu-1.hf.co":
		return true
	default:
		return false
	}
}

func (d *weightDownload) open(ctx context.Context, p *Pack, f config.ManifestFile) (io.ReadCloser, error) {
	original, err := url.Parse("https://huggingface.co/" + p.Repository + "/resolve/" + p.Revision + "/" + f.Path)
	if err != nil {
		return nil, errors.New("modelpack: cannot construct pinned file URL")
	}
	u := original
	client := *d.client
	client.Jar = nil
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	for hop := 0; ; hop++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !allowedWeightURL(u, original) {
			return nil, errors.New("modelpack: download redirect rejected")
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
		if err != nil {
			return nil, errors.New("modelpack: cannot construct download request")
		}
		req.Header.Set("Accept-Encoding", "identity")
		res, err := client.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, errors.New("modelpack: download request failed")
		}
		switch res.StatusCode {
		case 301, 302, 303, 307, 308:
			next, err := res.Location()
			res.Body.Close()
			if err != nil || hop >= 4 {
				return nil, errors.New("modelpack: invalid or excessive download redirects")
			}
			u = next
			continue
		}
		if res.StatusCode != http.StatusOK {
			res.Body.Close()
			return nil, fmt.Errorf("modelpack: download HTTP status %d", res.StatusCode)
		}
		if (res.ContentLength >= 0 && res.ContentLength != f.Bytes) || res.Uncompressed ||
			(res.Header.Get("Content-Encoding") != "" && res.Header.Get("Content-Encoding") != "identity") {
			res.Body.Close()
			return nil, errors.New("modelpack: download length or encoding rejected")
		}
		return res.Body, nil
	}
}

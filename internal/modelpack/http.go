// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

const (
	maxResponse  = 4 << 20
	maxSmallFile = 16 << 20
	maxTransfer  = 64 << 20
	maxPages     = 32
	maxFiles     = 10000
)

type remote struct {
	base   string
	client *http.Client
}

func newRemote() *remote {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxResponseHeaderBytes = 64 << 10
	return &remote{base: "https://huggingface.co", client: &http.Client{Timeout: 30 * time.Second, Transport: transport}}
}

type transfer struct {
	r     *remote
	bytes int64
}

// get enforces a shared byte budget, bounded bodies and narrowly approved
// redirects. Error bodies and underlying transport errors are never returned.
func (t *transfer) get(ctx context.Context, u *url.URL, limit int64, headers http.Header, redirect func(*url.URL) bool) ([]byte, http.Header, int, error) {
	if u.Scheme != "https" || u.User != nil || u.Fragment != "" {
		return nil, nil, 0, errors.New("modelpack: unsafe request URL")
	}
	if limit > maxTransfer-t.bytes {
		return nil, nil, 0, errors.New("modelpack: aggregate transfer limit")
	}
	client := *t.r.client
	client.Jar = nil
	client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if len(via) >= 5 || redirect == nil || !redirect(req.URL) || req.URL.User != nil || req.URL.Fragment != "" || req.URL.Scheme != "https" {
			return errors.New("modelpack: unsafe redirect")
		}
		return nil
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, nil, 0, errors.New("modelpack: cannot construct request")
	}
	for k, values := range headers {
		req.Header[k] = append([]string(nil), values...)
	}
	req.Header.Set("Accept-Encoding", "identity")
	resp, err := client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, 0, ctx.Err()
		}
		return nil, nil, 0, errors.New("modelpack: metadata request failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, resp.Header, resp.StatusCode, fmt.Errorf("modelpack: metadata HTTP status %d", resp.StatusCode)
	}
	if resp.ContentLength > limit {
		return nil, nil, resp.StatusCode, errors.New("modelpack: response exceeds byte limit")
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	t.bytes += int64(len(b))
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, 0, ctx.Err()
		}
		return nil, nil, 0, errors.New("modelpack: metadata read failed")
	}
	if int64(len(b)) > limit {
		return nil, nil, resp.StatusCode, errors.New("modelpack: response exceeds byte limit")
	}
	return b, resp.Header, resp.StatusCode, nil
}

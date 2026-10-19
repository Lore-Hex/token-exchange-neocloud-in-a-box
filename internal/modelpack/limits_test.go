// SPDX-License-Identifier: Apache-2.0
package modelpack

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func TestPageAndPathLimits(t *testing.T) {
	i, tr, b := fixture()
	r := fakeRemote(t, i, tr, b)
	base := r.client.Transport
	pages := 0
	r.client.Transport = roundTrip(func(req *http.Request) (*http.Response, error) {
		if !strings.Contains(req.URL.Path, "/tree/") {
			return base.RoundTrip(req)
		}
		pages++
		res := response("[]")
		u := *req.URL
		q := u.Query()
		q.Set("cursor", fmt.Sprint(pages))
		u.RawQuery = q.Encode()
		res.Header.Set("Link", "<"+u.String()+">; rel=\"next\"")
		return res, nil
	})
	if _, err := r.generate(context.Background(), "org/model", testRev); err == nil || pages != maxPages {
		t.Fatal("page cap not enforced")
	}
	var siblings []map[string]string
	for n := 0; n <= maxFiles; n++ {
		siblings = append(siblings, map[string]string{"rfilename": fmt.Sprintf("file%d", n)})
	}
	i["siblings"] = siblings
	if _, err := fakeRemote(t, i, tr, b).generate(context.Background(), "org/model", testRev); err == nil {
		t.Fatal("sibling cap not enforced")
	}
	for _, p := range []string{"a/./b", "a/../b", strings.Repeat("a", 1025), "/absolute", "a\nb", "a\\b"} {
		if validPath(p) {
			t.Fatal("unsafe path accepted")
		}
	}
}

func TestAggregatePreflight(t *testing.T) {
	i, tr, b := fixture()
	var rows []treeFile
	var siblings []map[string]string
	for n := 0; n < 5; n++ {
		f := tr[1]
		f.Path = fmt.Sprintf("small%d", n)
		f.Size = ptr(maxSmallFile)
		rows = append(rows, f)
		siblings = append(siblings, map[string]string{"rfilename": f.Path})
	}
	i["siblings"] = siblings
	if _, err := fakeRemote(t, i, rows, b).generate(context.Background(), "org/model", testRev); err == nil || !strings.Contains(err.Error(), "aggregate") {
		t.Fatal("aggregate file cap not enforced before fetching")
	}
}

type errorReader struct{}

func (errorReader) Read([]byte) (int, error) { return 0, errors.New("SECRET_READ") }
func TestReadFailureAndRedirectBodyBudget(t *testing.T) {
	u, _ := url.Parse("https://huggingface.co/fixed")
	for _, kind := range []string{"read", "loop"} {
		t.Run(kind, func(t *testing.T) {
			body := &countingReader{remaining: 1 << 30}
			calls := 0
			r := &remote{client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
				calls++
				res := response("")
				res.Request = req
				if kind == "read" {
					res.Body = io.NopCloser(errorReader{})
				} else {
					res.StatusCode = 307
					res.Header.Set("Location", u.String())
					res.Body = io.NopCloser(body)
				}
				return res, nil
			})}}
			tr := transfer{r: r}
			_, _, _, err := tr.get(context.Background(), u, 64, nil, func(*url.URL) bool { return true })
			if err == nil || strings.Contains(err.Error(), "SECRET") {
				t.Fatal("read/redirect failure accepted or leaked")
			}
			if body.read != 0 || (kind == "loop" && calls != 5) {
				t.Fatal("redirect body downloaded or hops unbounded")
			}
		})
	}
}

func TestMalformedJSONAndLocalFiles(t *testing.T) {
	for _, body := range []string{"null", "[]", "{bad", "{} {}"} {
		r := &remote{base: "https://huggingface.co", client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) { return response(body), nil })}}
		if _, err := r.generate(context.Background(), "org/model", testRev); err == nil {
			t.Fatal("bad identity accepted")
		}
	}
	i, tr, b := fixture()
	r := fakeRemote(t, i, tr, b)
	m, err := r.generate(context.Background(), "org/model", testRev)
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"yaml", "total", "oversize", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			d := writePack(t, m)
			p := filepath.Join(d, "pack.yaml")
			data, _ := os.ReadFile(p)
			switch kind {
			case "yaml":
				data = []byte("invalid: SECRET")
			case "total":
				data = []byte(strings.Replace(string(data), fmt.Sprint(m.TotalBytes), "123", 1))
			case "oversize":
				data = []byte(strings.Repeat("x", config.MaxDocumentBytes+1))
			case "symlink":
				if err := os.Symlink(d, filepath.Join(t.TempDir(), "link")); err != nil {
					t.Fatal(err)
				}
				if _, _, err := Load(filepath.Join(d, "missing/SECRET")); err == nil || strings.Contains(err.Error(), "SECRET") {
					t.Fatal("path error leaked")
				}
				if err := os.Remove(p); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(filepath.Join(d, "files.sha256"), p); err != nil {
					t.Fatal(err)
				}
			}
			if kind != "symlink" {
				if err := os.WriteFile(p, data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			if _, _, err := Load(d); err == nil || strings.Contains(err.Error(), "SECRET") {
				t.Fatal("bad local file accepted or leaked")
			}
		})
	}
}

func TestRealPackDrafts(t *testing.T) {
	for _, tc := range []struct {
		name, repo, revision string
		total                int64
		files                int
	}{
		{"deepseek-v4.1-flash-1017", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "1bae0e1088993b9b82de08f59de5be5a34e42170", 510311045295, 83},
		{"glm-5.3", "zai-org/GLM-5.3", "ba32f8c19c8738e2c57c71798c601d477a7bf844", 755663689302, 156},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := filepath.Join("..", "..", "models", tc.name, "1")
			p, b, err := Load(d)
			if err != nil {
				t.Fatal(err)
			}
			if p.Repository != tc.repo || p.Revision != tc.revision || p.TotalBytes != tc.total || len(strings.Split(strings.TrimSpace(string(b)), "\n")) != tc.files {
				t.Fatal("pack identity or totals changed")
			}
			yaml, err := os.ReadFile(filepath.Join(d, "pack.yaml"))
			if err != nil {
				t.Fatal(err)
			}
			if config.ValidateDeployableModelPack(yaml) == nil {
				t.Fatal("draft pack became deployable")
			}
			if len(p.EngineProfiles) != 1 {
				t.Fatal("unexpected engine profile")
			}
			if _, ok := p.EngineProfiles["b200x8-gcp-plain"]; !ok {
				t.Fatal("unexpected confidential claim")
			}
		})
	}
}

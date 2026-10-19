// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"bytes"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

const testRev = "1111111111111111111111111111111111111111"
const testHash = "2222222222222222222222222222222222222222222222222222222222222222"

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(b string) *http.Response {
	return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(b)), ContentLength: -1}
}
func jsonText(v any) string { b, _ := json.Marshal(v); return string(b) }
func ptr(n int64) *int64    { return &n }
func fixture() (map[string]any, []treeFile, string) {
	body := "test configuration\n"
	blob := fmt.Sprintf("%x", sha1.Sum([]byte(fmt.Sprintf("blob %d%c%s", len(body), 0, body))))
	identity := map[string]any{"id": "org/model", "sha": testRev, "siblings": []map[string]string{{"rfilename": "config.json"}, {"rfilename": "weight.safetensors"}}}
	var tree []treeFile
	_ = json.Unmarshal([]byte(fmt.Sprintf(`[{"type":"file","path":"weight.safetensors","size":1000000000000,"lfs":{"size":1000000000000,"oid":"%s"}},{"type":"file","path":"config.json","size":%d,"oid":"%s"}]`, testHash, len(body), blob)), &tree)
	return identity, tree, body
}
func fakeRemote(t *testing.T, identity map[string]any, tree []treeFile, body string) *remote {
	t.Helper()
	return &remote{base: "https://huggingface.co", client: &http.Client{Transport: roundTrip(func(req *http.Request) (*http.Response, error) {
		if req.Header.Get("Authorization") != "" || req.Header.Get("Cookie") != "" {
			t.Fatal("credentials sent to HF")
		}
		switch {
		case strings.Contains(req.URL.Path, "/revision/"):
			return response(jsonText(identity)), nil
		case strings.Contains(req.URL.Path, "/tree/"):
			return response(jsonText(tree)), nil
		case strings.HasSuffix(req.URL.Path, "/config.json"):
			return response(body), nil
		default:
			t.Fatalf("unexpected request, possibly weights: %s", req.URL.Path)
			return nil, errors.New("unexpected")
		}
	})}}
}
func TestGenerateCompleteManifest(t *testing.T) {
	i, tree, body := fixture()
	r := fakeRemote(t, i, tree, body)
	m, err := r.generate(context.Background(), "org/model", testRev)
	if err != nil {
		t.Fatal(err)
	}
	if len(m.Files) != 2 || m.TotalBytes != 1000000000000+int64(len(body)) || m.Files[0].Path != "config.json" || m.Files[0].SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(body))) || m.Files[1].SHA256 != testHash {
		t.Fatal("wrong manifest")
	}
	if m.DownloadedBytes > 4096 {
		t.Fatal("weight bytes downloaded")
	}
	b, err := m.MarshalText()
	if err != nil || config.ValidateFileManifest(b, m.TotalBytes) != nil {
		t.Fatal("invalid manifest")
	}
}
func TestInvalidInputsNoNetwork(t *testing.T) {
	r := &remote{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("network reached"); return nil, nil })}}
	for _, pair := range [][2]string{{"org/model", "main"}, {"org/model", strings.Repeat("0", 40)}, {"https://secret/a", testRev}, {"org/../secret", testRev}, {"org/model?token=secret", testRev}, {"org/model#secret", testRev}, {"org\\model", testRev}} {
		if _, err := r.generate(context.Background(), pair[0], pair[1]); err == nil || strings.Contains(err.Error(), "secret") {
			t.Fatal("invalid input accepted or leaked")
		}
	}
}
func TestAdverseMetadata(t *testing.T) {
	tests := map[string]func(map[string]any, *[]treeFile, *string){
		"identity":         func(i map[string]any, _ *[]treeFile, _ *string) { i["id"] = "evil/model" },
		"revision":         func(i map[string]any, _ *[]treeFile, _ *string) { i["sha"] = "main" },
		"siblings_missing": func(i map[string]any, _ *[]treeFile, _ *string) { delete(i, "siblings") },
		"siblings_duplicate": func(i map[string]any, _ *[]treeFile, _ *string) {
			i["siblings"] = []map[string]string{{"rfilename": "config.json"}, {"rfilename": "config.json"}}
		},
		"incomplete":    func(_ map[string]any, tr *[]treeFile, _ *string) { *tr = (*tr)[:1] },
		"duplicate":     func(_ map[string]any, tr *[]treeFile, _ *string) { *tr = append(*tr, (*tr)[0]) },
		"traversal":     func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].Path = "../secret" },
		"encoded":       func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].Path = "a%2fb" },
		"type":          func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].Type = "symlink" },
		"size_missing":  func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].Size = nil },
		"size_negative": func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].Size = ptr(-1) },
		"size_overflow": func(_ map[string]any, tr *[]treeFile, _ *string) {
			(*tr)[0].Size = ptr(math.MaxInt64)
			(*tr)[0].LFS.Size = ptr(math.MaxInt64)
		},
		"lfs_missing_size": func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].LFS.Size = nil },
		"lfs_mismatch":     func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].LFS.Size = ptr(1) },
		"lfs_hash":         func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].LFS.OID = "sha256:" + testHash },
		"lfs_zero":         func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].LFS.OID = strings.Repeat("0", 64) },
		"no_lfs_large":     func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[0].LFS = nil },
		"git_oid":          func(_ map[string]any, tr *[]treeFile, _ *string) { (*tr)[1].OID = testRev },
		"size_short":       func(_ map[string]any, _ *[]treeFile, b *string) { *b = "short" },
		"size_long":        func(_ map[string]any, _ *[]treeFile, b *string) { *b += "extra" },
		"content_hash":     func(_ map[string]any, _ *[]treeFile, b *string) { *b = strings.Repeat("X", len(*b)) },
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			i, tr, b := fixture()
			change(i, &tr, &b)
			if _, err := fakeRemote(t, i, tr, b).generate(context.Background(), "org/model", testRev); err == nil {
				t.Fatal("bad metadata accepted")
			}
		})
	}
}
func TestPaginationPolicy(t *testing.T) {
	initial, _ := url.Parse("https://huggingface.co/api/models/org/model/tree/" + testRev + "?expand=false&recursive=true&limit=1000")
	good := initial.String() + "&cursor=next"
	link := func(u string) string { return "<" + u + ">; rel=\"next\"" }
	if u, err := nextPage(initial, []string{link(good)}); err != nil || u.Query().Get("cursor") != "next" {
		t.Fatal("valid pagination rejected")
	}
	for _, bad := range []string{
		strings.Replace(good, "https:", "http:", 1), strings.Replace(good, "huggingface.co", "evil.invalid", 1), strings.Replace(good, testRev, "main", 1), good + "&token=SECRET", good + "&cursor=again", good + "#SECRET", strings.Replace(good, "https://", "https://SECRET@", 1), strings.Replace(good, "limit=1000", "limit=1", 1), strings.Replace(good, "/model/", "/%6dodel/", 1), initial.String(), good + "&x=%ZZ",
	} {
		if _, err := nextPage(initial, []string{link(bad)}); err == nil {
			t.Fatalf("accepted bad link %s", bad)
		}
	}
	for _, hs := range [][]string{{"garbage SECRET"}, {link(good), link(good)}, {strings.Repeat("x", 8193)}, {"<" + good + ">"}} {
		if _, err := nextPage(initial, hs); err == nil || strings.Contains(err.Error(), "SECRET") {
			t.Fatal("malformed header accepted or leaked")
		}
	}
}
func TestMultiplePagesAndCycle(t *testing.T) {
	for _, cycle := range []bool{false, true} {
		t.Run(fmt.Sprint(cycle), func(t *testing.T) {
			i, tr, b := fixture()
			r := fakeRemote(t, i, tr, b)
			base := r.client.Transport
			r.client.Transport = roundTrip(func(req *http.Request) (*http.Response, error) {
				if !strings.Contains(req.URL.Path, "/tree/") {
					return base.RoundTrip(req)
				}
				res := response(jsonText(tr[:1]))
				if req.URL.Query().Get("cursor") != "" {
					res = response(jsonText(tr[1:]))
					if !cycle {
						return res, nil
					}
					res = response("[]")
				}
				u := *req.URL
				q := u.Query()
				q.Set("cursor", "next")
				u.RawQuery = q.Encode()
				res.Header.Set("Link", "<"+u.String()+">; rel=\"next\"")
				return res, nil
			})
			_, err := r.generate(context.Background(), "org/model", testRev)
			if (err != nil) != cycle {
				t.Fatalf("pagination result: %v", err)
			}
		})
	}
}

type countingReader struct{ remaining, read int64 }

func (c *countingReader) Read(p []byte) (int, error) {
	if c.remaining == 0 {
		return 0, io.EOF
	}
	n := min(int64(len(p)), c.remaining)
	clear(p[:n])
	c.remaining -= n
	c.read += n
	return int(n), nil
}
func TestHTTPBoundsAndSanitization(t *testing.T) {
	u, _ := url.Parse("https://huggingface.co/fixed")
	for _, known := range []bool{true, false} {
		body := &countingReader{remaining: 1000}
		r := &remote{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
			res := response("")
			res.Body = io.NopCloser(body)
			if known {
				res.ContentLength = 1000
			}
			return res, nil
		})}}
		tr := transfer{r: r}
		if _, _, _, err := tr.get(context.Background(), u, 8, nil, nil); err == nil {
			t.Fatal("unbounded response")
		}
		if body.read > 9 || (known && body.read != 0) {
			t.Fatal("read past cap")
		}
	}
	for _, kind := range []string{"network", "status", "redirect", "canceled", "budget"} {
		t.Run(kind, func(t *testing.T) {
			r := &remote{client: &http.Client{Transport: roundTrip(func(*http.Request) (*http.Response, error) {
				if kind == "network" || kind == "canceled" {
					return nil, errors.New("SECRET_URL SECRET_BODY")
				}
				res := response("SECRET_BODY")
				res.StatusCode = 500
				if kind == "redirect" {
					res.StatusCode = 302
					res.Header.Set("Location", "https://evil.invalid/SECRET_URL")
				}
				return res, nil
			})}}
			ctx := context.Background()
			if kind == "canceled" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			tr := transfer{r: r}
			if kind == "budget" {
				tr.bytes = maxTransfer
			}
			_, _, _, err := tr.get(ctx, u, 8, nil, nil)
			if err == nil || strings.Contains(err.Error(), "SECRET") {
				t.Fatal("failure accepted or leaked")
			}
			if kind == "canceled" && !errors.Is(err, context.Canceled) {
				t.Fatal("lost cancellation")
			}
		})
	}
}
func writePack(t *testing.T, m *Manifest) string {
	t.Helper()
	d := t.TempDir()
	b, err := m.MarshalText()
	if err != nil {
		t.Fatal(err)
	}
	p := fmt.Sprintf("schema: nib.model-pack/v1\nid: test@1\nhf_repo: org/model\nhf_revision: '%s'\nlicense: MIT\nserved_model_name: test\ntr_model_id: org/test\nfiles: files.sha256\ntotal_bytes: %d\nweights_volume: {format: erofs, verity_root_hash: ''}\nengine_profiles: {}\n", testRev, m.TotalBytes)
	if err := os.WriteFile(filepath.Join(d, "pack.yaml"), []byte(p), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(d, "files.sha256"), b, 0600); err != nil {
		t.Fatal(err)
	}
	return d
}
func TestCheckRemoteAndLocalSafety(t *testing.T) {
	i, tr, b := fixture()
	r := fakeRemote(t, i, tr, b)
	m, err := r.generate(context.Background(), "org/model", testRev)
	if err != nil {
		t.Fatal(err)
	}
	d := writePack(t, m)
	if err := checkRemote(context.Background(), d, r); err != nil {
		t.Fatal(err)
	}
	if err := checkRemote(context.Background(), filepath.Join(d, "pack.yaml"), r); err != nil {
		t.Fatal(err)
	}
	packBytes, _ := os.ReadFile(filepath.Join(d, "pack.yaml"))
	if config.ValidateDeployableModelPack(packBytes) == nil {
		t.Fatal("draft deployable")
	}
	tr[0].LFS.OID = strings.Repeat("3", 64)
	if err := checkRemote(context.Background(), d, fakeRemote(t, i, tr, b)); err == nil {
		t.Fatal("changed remote hash accepted")
	}
	if err := os.Remove(filepath.Join(d, "files.sha256")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(d, "pack.yaml"), filepath.Join(d, "files.sha256")); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Load(d); err == nil {
		t.Fatal("symlink accepted")
	}
	if err := os.Remove(filepath.Join(d, "files.sha256")); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(d, "files.sha256"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Load(d); err == nil {
		t.Fatal("directory accepted")
	}
}
func TestCLIAndMissingPathRedaction(t *testing.T) {
	for _, tc := range []struct {
		args []string
		code int
	}{{[]string{"help"}, 0}, {[]string{"--help"}, 0}, {nil, 2}, {[]string{"wrong", "SECRET"}, 2}, {[]string{"check-remote", "/missing/SECRET"}, 1}} {
		var out, errs bytes.Buffer
		if code := Run(context.Background(), tc.args, &out, &errs); code != tc.code || strings.Contains(out.String()+errs.String(), "SECRET") {
			t.Fatal("CLI status or redaction")
		}
	}
}

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"crypto/sha1" // Git blob identity only; manifest contents use SHA-256.
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"mime"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	repoPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$`)
	pathPattern   = regexp.MustCompile(`^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$`)
	commitPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	hashPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

func validHash(s string) bool   { return hashPattern.MatchString(s) && strings.Trim(s, "0") != "" }
func validCommit(s string) bool { return commitPattern.MatchString(s) && strings.Trim(s, "0") != "" }
func validPath(s string) bool {
	if len(s) > 1024 || !pathPattern.MatchString(s) {
		return false
	}
	for _, p := range strings.Split(s, "/") {
		if p == "." || p == ".." {
			return false
		}
	}
	return true
}

type treeFile struct {
	Type string `json:"type"`
	Path string `json:"path"`
	Size *int64 `json:"size"`
	OID  string `json:"oid"`
	LFS  *struct {
		OID  string `json:"oid"`
		Size *int64 `json:"size"`
	} `json:"lfs"`
}

func (r *remote) generate(ctx context.Context, repo, rev string) (*Manifest, error) {
	if len(repo) > 256 || !repoPattern.MatchString(repo) || !validCommit(rev) {
		return nil, errors.New("modelpack: repository and immutable revision required")
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	t := transfer{r: r}
	identityURL, _ := url.Parse(r.base + "/api/models/" + repo + "/revision/" + rev)
	b, _, _, err := t.get(ctx, identityURL, maxResponse, nil, nil)
	if err != nil {
		return nil, fmt.Errorf("HF identity: %w", err)
	}
	var identity struct {
		ID       string `json:"id"`
		SHA      string `json:"sha"`
		Siblings []struct {
			Path string `json:"rfilename"`
		} `json:"siblings"`
	}
	if json.Unmarshal(b, &identity) != nil || identity.ID != repo || identity.SHA != rev || len(identity.Siblings) == 0 || len(identity.Siblings) > maxFiles {
		return nil, errors.New("modelpack: remote identity mismatch or incomplete sibling list")
	}
	siblings := make(map[string]bool)
	for _, f := range identity.Siblings {
		if !validPath(f.Path) || siblings[f.Path] {
			return nil, errors.New("modelpack: invalid remote sibling list")
		}
		siblings[f.Path] = true
	}
	initial, _ := url.Parse(r.base + "/api/models/" + repo + "/tree/" + rev + "?expand=false&recursive=true&limit=1000")
	next := initial
	seenPages := make(map[string]bool)
	seenPaths := make(map[string]bool)
	var entries []treeFile
	var smallBytes, total int64
	for page := 0; next != nil; page++ {
		if page >= maxPages || seenPages[next.String()] {
			return nil, errors.New("modelpack: pagination limit or cycle")
		}
		seenPages[next.String()] = true
		b, headers, _, err := t.get(ctx, next, maxResponse, nil, nil)
		if err != nil {
			return nil, fmt.Errorf("HF tree: %w", err)
		}
		var batch []treeFile
		if json.Unmarshal(b, &batch) != nil || batch == nil {
			return nil, errors.New("modelpack: invalid tree JSON")
		}
		for _, f := range batch {
			if !validPath(f.Path) || seenPaths[f.Path] || len(seenPaths) >= maxFiles {
				return nil, errors.New("modelpack: invalid, repeated or excessive tree path")
			}
			seenPaths[f.Path] = true
			if f.Type == "directory" {
				continue
			}
			if f.Type != "file" || f.Size == nil || *f.Size < 0 || *f.Size > math.MaxInt64-total || !siblings[f.Path] {
				return nil, errors.New("modelpack: invalid tree file or size")
			}
			total += *f.Size
			if f.LFS != nil {
				if f.LFS.Size == nil || *f.LFS.Size != *f.Size || !validHash(f.LFS.OID) {
					return nil, errors.New("modelpack: invalid LFS size or SHA-256")
				}
			} else {
				if *f.Size > maxSmallFile || !validCommit(f.OID) {
					return nil, errors.New("modelpack: non-LFS file too large or missing Git blob identity")
				}
				smallBytes += *f.Size
				if smallBytes > maxTransfer {
					return nil, errors.New("modelpack: non-LFS aggregate limit")
				}
			}
			entries = append(entries, f)
		}
		next, err = nextPage(initial, headers.Values("Link"))
		if err != nil {
			return nil, err
		}
	}
	if len(entries) != len(siblings) {
		return nil, errors.New("modelpack: tree does not cover every repository file")
	}
	if smallBytes > maxTransfer-t.bytes {
		return nil, errors.New("modelpack: aggregate transfer limit")
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
	m := &Manifest{Repository: repo, Revision: rev, TotalBytes: total}
	for _, e := range entries {
		f := File{Path: e.Path, Bytes: *e.Size}
		if e.LFS != nil {
			f.SHA256 = e.LFS.OID
		} else {
			u, _ := url.Parse(r.base + "/" + repo + "/resolve/" + rev + "/" + e.Path)
			cachePath := "/api/resolve-cache/models/" + repo + "/" + rev + "/" + e.Path
			// HF also encodes the entire nested filename as one cache-path
			// component. Accept only that canonical encoding of this exact file.
			cacheEncoded := "/api/resolve-cache/models/" + repo + "/" + rev + "/" + url.PathEscape(e.Path)
			allow := func(v *url.URL) bool {
				return v.Host == u.Host && ((v.Path == u.Path && v.EscapedPath() == u.EscapedPath()) ||
					(v.Path == cachePath && (v.EscapedPath() == cachePath || v.EscapedPath() == cacheEncoded)))
			}
			b, _, _, err := t.get(ctx, u, *e.Size, nil, allow)
			if err != nil {
				return nil, fmt.Errorf("HF non-LFS content: %w", err)
			}
			if int64(len(b)) != *e.Size {
				return nil, errors.New("modelpack: non-LFS size mismatch")
			}
			blob := sha1.New()
			fmt.Fprintf(blob, "blob %d%c", len(b), 0)
			_, _ = blob.Write(b)
			if fmt.Sprintf("%x", blob.Sum(nil)) != e.OID {
				return nil, errors.New("modelpack: non-LFS Git blob mismatch")
			}
			f.SHA256 = fmt.Sprintf("%x", sha256.Sum256(b))
		}
		m.Files = append(m.Files, f)
	}
	m.DownloadedBytes = t.bytes
	if _, err := m.MarshalText(); err != nil {
		return nil, err
	}
	return m, nil
}

// Only the documented HF Link shape is accepted. Any ambiguous/unsupported
// header fails closed. The next URL must retain origin, repository, revision
// and listing options; only an opaque cursor may change.
func nextPage(initial *url.URL, headers []string) (*url.URL, error) {
	bad := errors.New("modelpack: invalid pagination link")
	var next *url.URL
	n := 0
	for _, h := range headers {
		n += len(h)
		if n > 8192 {
			return nil, bad
		}
		for _, part := range strings.Split(h, ",") {
			part = strings.TrimSpace(part)
			if !strings.HasPrefix(part, "<") {
				return nil, bad
			}
			target, params, ok := strings.Cut(part[1:], ">")
			_, p, err := mime.ParseMediaType("link" + params)
			if !ok || err != nil || p["rel"] == "" {
				return nil, bad
			}
			isNext := false
			for _, rel := range strings.Fields(p["rel"]) {
				isNext = isNext || rel == "next"
			}
			if !isNext {
				continue
			}
			u, err := url.Parse(target)
			if err != nil || next != nil {
				return nil, bad
			}
			u = initial.ResolveReference(u)
			if u.Scheme != "https" || u.Host != initial.Host || u.User != nil || u.Fragment != "" || u.Path != initial.Path || u.RawPath != "" {
				return nil, bad
			}
			q, err := url.ParseQuery(u.RawQuery)
			if err != nil || q.Get("cursor") == "" || q.Get("expand") != "false" || q.Get("recursive") != "true" || q.Get("limit") != "1000" {
				return nil, bad
			}
			for k, vs := range q {
				if len(vs) != 1 || (k != "cursor" && k != "expand" && k != "recursive" && k != "limit") {
					return nil, bad
				}
			}
			u.RawQuery = q.Encode()
			next = u
		}
	}
	return next, nil
}

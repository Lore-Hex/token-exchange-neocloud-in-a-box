// SPDX-License-Identifier: Apache-2.0
package modelpack

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

func TestPinnedFileRedirects(t *testing.T) {
	for _, tc := range []struct {
		name, location string
		pass           bool
	}{
		{"cache_nested", "/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fconfig.json", true},
		{"cache_plain", "/api/resolve-cache/models/org/model/" + testRev + "/folder/config.json", true},
		{"other_commit", "/api/resolve-cache/models/org/model/" + strings.Repeat("2", 40) + "/folder%2Fconfig.json", false},
		{"wrong_file", "/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fother.json", false},
		{"wrong_host", "https://evil.invalid/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fconfig.json", false},
		{"http", "http://huggingface.co/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fconfig.json", false},
		{"encoded_name", "/api/resolve-cache/models/org/model/" + testRev + "/folder%2F%63onfig.json", false},
		{"userinfo", "https://SECRET@huggingface.co/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fconfig.json", false},
		{"fragment", "/api/resolve-cache/models/org/model/" + testRev + "/folder%2Fconfig.json#SECRET", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			i, tr, b := fixture()
			tr[1].Path = "folder/config.json"
			i["siblings"] = []map[string]string{{"rfilename": "folder/config.json"}, {"rfilename": "weight.safetensors"}}
			r := fakeRemote(t, i, tr, b)
			base := r.client.Transport
			r.client.Transport = roundTrip(func(req *http.Request) (*http.Response, error) {
				if strings.Contains(req.URL.Path, "/resolve/") {
					res := response("")
					res.StatusCode = 307
					res.Header.Set("Location", tc.location)
					return res, nil
				}
				if strings.Contains(req.URL.Path, "/resolve-cache/") {
					if !tc.pass {
						t.Fatal("unsafe redirect followed")
					}
					return response(b), nil
				}
				return base.RoundTrip(req)
			})
			_, err := r.generate(context.Background(), "org/model", testRev)
			if (err == nil) != tc.pass {
				t.Fatalf("unexpected redirect result: %v", err)
			}
			if err != nil && strings.Contains(err.Error(), "SECRET") {
				t.Fatal("redirect leaked")
			}
		})
	}
}

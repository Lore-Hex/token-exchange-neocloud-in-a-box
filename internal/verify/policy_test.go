// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"
)

func digest(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func policyFiles(t *testing.T) ([]byte, []byte) {
	t.Helper()
	r, err := os.ReadFile("../config/testdata/valid/release.json")
	if err != nil {
		t.Fatal(err)
	}
	p, err := os.ReadFile("../config/testdata/valid/hardware-profile.json")
	if err != nil {
		t.Fatal(err)
	}
	return r, p
}

func testPolicy(t *testing.T) Policy {
	t.Helper()
	r, p := policyFiles(t)
	policy, err := LoadPolicy(r, digest(r), p, digest(p))
	if err != nil {
		t.Fatal(err)
	}
	return policy
}

func TestPinnedPolicy(t *testing.T) {
	r, p := policyFiles(t)
	policy := testPolicy(t)
	if !policy.valid || policy.gpus != 2 || policy.switches != 0 || policy.profileID != "b200x2-tdx" {
		t.Fatal("wrong validated policy")
	}
	for _, tc := range []struct {
		name             string
		release, profile []byte
		rHash, pHash     string
	}{
		{"release-pin", r, p, strings.Repeat("1", 64), digest(p)},
		{"profile-pin", r, p, digest(r), strings.Repeat("2", 64)},
		{"empty-pin", r, p, "", digest(p)},
		{"release-tamper", append(append([]byte{}, r...), '\n'), p, digest(r), digest(p)},
		{"profile-tamper", r, append(append([]byte{}, p...), '\n'), digest(r), digest(p)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := LoadPolicy(tc.release, tc.rHash, tc.profile, tc.pHash); err == nil {
				t.Fatal("unpinned policy accepted")
			}
		})
	}
	for _, bad := range [][]byte{
		bytes.Replace(p, []byte(`"blackwell-mgpu-cc"`), []byte(`"none"`), 1),
		bytes.Replace(p, []byte(`"b200x2-tdx"`), []byte(`"unknown-profile"`), 1),
		[]byte(`{"schema":"secret-invalid"}`),
	} {
		if _, err := LoadPolicy(r, digest(r), bad, digest(bad)); err == nil {
			t.Fatal("unacceptable hardware policy accepted")
		}
	}
	badRelease := bytes.Replace(r, []byte(`"release"`), []byte(`"debug"`), 1)
	if _, err := LoadPolicy(badRelease, digest(badRelease), p, digest(p)); err == nil {
		t.Fatal("debug manifest accepted")
	}
}

func TestZeroPolicyCannotVerify(t *testing.T) {
	result, err := Check(context.Background(), Observation{}, Policy{})
	if err == nil || result.Confidential || result.Status != "fail" {
		t.Fatal("unvalidated policy accepted")
	}
}

func TestVerifyCLIRejectsMissingPolicyWithoutEchoingInput(t *testing.T) {
	for _, args := range [][]string{nil, {"--secret-user-input"}, {"--endpoint=https://secret-user-input"}} {
		var out, errOut bytes.Buffer
		if Run(args, &out, &errOut) != 2 || strings.Contains(errOut.String(), "secret-user-input") || out.Len() != 0 {
			t.Fatal("CLI validation leaked arguments or proceeded")
		}
	}
}

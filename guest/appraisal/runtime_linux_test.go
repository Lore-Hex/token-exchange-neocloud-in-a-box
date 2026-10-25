//go:build linux && amd64

// SPDX-License-Identifier: Apache-2.0
package appraisal

import (
	"context"
	"strings"
	"syscall"
	"testing"
)

func TestPrivilegeBoundary(t *testing.T) {
	raw := "Uid:\t999 999 999 999\nGid:\t999 999 999 999\nGroups:\t\nCapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\nCapAmb:\t0000000000000000\nNoNewPrivs:\t1\nSeccomp:\t2\n"
	if !privilegeStatus([]byte(raw), 999, 999) {
		t.Fatal("fixture")
	}
	for _, line := range strings.Split(strings.TrimSuffix(raw, "\n"), "\n") {
		if privilegeStatus([]byte(strings.Replace(raw, line+"\n", "", 1)), 999, 999) {
			t.Fatal("missing privilege fact admitted")
		}
	}
	for _, bad := range []string{raw + "Seccomp:\t2\n", strings.Replace(raw, "999 999 999 999", "999 999 0 999", 1), strings.Replace(raw, "Groups:\t", "Groups:\t0", 1), strings.Replace(raw, "NoNewPrivs:\t1", "NoNewPrivs:\t0", 1), strings.Replace(raw, "Seccomp:\t2", "Seccomp:\t0", 1), strings.Replace(raw, "0000000000000000", "0000000000000001", 1)} {
		if privilegeStatus([]byte(bad), 999, 999) {
			t.Fatal("privilege escalation admitted")
		}
	}
	if privilegeStatus([]byte(raw), 0, 0) {
		t.Fatal("root admitted")
	}
}

func TestNoAmbientHostOrArgumentBypass(t *testing.T) {
	if attr := processAttributes(); !attr.Setpgid || attr.Pdeathsig != syscall.SIGKILL || attr.Credential != nil {
		t.Fatal("process ownership/parent death boundary")
	}
	for _, args := range [][]string{nil, {"--help"}, {"--collect-and-appraise", "--deadline", "2099-01-01T00:00:00Z"}, {"--collect-and-appraise", "--deadline", "2000-01-01T00:00:00Z"}, {"--collect-and-appraise", "--deadline", "2000-01-01T00:00:00Z", "--nras-url", "https://invalid"}} {
		r := Run(context.Background(), args)
		if r.Result != "fail" || r.AppraisalAccepted {
			t.Fatal("bad invocation accepted")
		}
	}
	if readPinned("/bin/sh", 1, executableSHA) == nil {
		t.Fatal("arbitrary executable")
	}
	// CI's ordinary writable host is not the admitted read-only guest.
	if runtimeCheck() == nil {
		t.Fatal("ordinary host admitted")
	}
}

//go:build linux && !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"syscall"
	"testing"
)

func TestNetworkProtectedInputMetadata(t *testing.T) {
	good := syscall.Stat_t{Mode: syscall.S_IFREG | 0o644, Nlink: 1, Size: 40}
	fs := syscall.Statfs_t{Type: 0xe0f5e1e2, Flags: 1}
	if networkInputFailure(good, fs, 4096, 0o644) != nil {
		t.Fatal("valid protected metadata rejected")
	}
	for _, test := range []struct {
		mutate func(*syscall.Stat_t)
		stage  networkFileFailure
	}{
		{func(s *syscall.Stat_t) { s.Mode = syscall.S_IFLNK | 0o644 }, failedFileMode},
		{func(s *syscall.Stat_t) { s.Mode = syscall.S_IFIFO | 0o644 }, failedFileMode},
		{func(s *syscall.Stat_t) { s.Mode |= 0o002 }, failedFileMode},
		{func(s *syscall.Stat_t) { s.Mode |= syscall.S_ISUID }, failedFileMode},
		{func(s *syscall.Stat_t) { s.Uid = 1 }, failedFileOwner},
		{func(s *syscall.Stat_t) { s.Gid = 1 }, failedFileOwner},
		{func(s *syscall.Stat_t) { s.Nlink = 0 }, failedFileLinks},
		{func(s *syscall.Stat_t) { s.Nlink = 2 }, failedFileLinks},
		{func(s *syscall.Stat_t) { s.Size = 0 }, failedFileSize},
		{func(s *syscall.Stat_t) { s.Size = 4097 }, failedFileSize},
	} {
		bad := good
		test.mutate(&bad)
		if networkInputFailure(bad, fs, 4096, 0o644) != test.stage {
			t.Fatal("unsafe input metadata accepted or incorrectly classified")
		}
	}
	for _, badFS := range []syscall.Statfs_t{{Type: 0xe0f5e1e2}, {Type: 0xef53, Flags: 1}} {
		if networkInputFailure(good, badFS, 4096, 0o644) != failedFileFilesystem {
			t.Fatal("mutable/other filesystem accepted")
		}
	}
	// No fixed input or executable is opened for an arbitrary caller path.
	for _, path := range []string{"", "guest/testdata/fake/guest.json", "/etc/nib/../nib/network.json"} {
		if runNetwork(context.Background(), path) == nil {
			t.Fatal("noncanonical path accepted")
		}
	}
}

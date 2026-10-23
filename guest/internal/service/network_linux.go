//go:build linux && !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net"
	"os"
	"strings"
	"syscall"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

const networkConfig = "/etc/nib/network.json"
const nftExecutable = "/usr/sbin/nft"

// Exact regular member of the pinned nftables_1.0.9-1ubuntu0.1_amd64.deb.
// Package and primary-source identities: docs/tasks/p3-t1-guest-network.md.
const nftSHA256 = "3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7"
const nftSize = 26856

func runNetwork(ctx context.Context, path string) error {
	ctx, cancel := context.WithTimeout(ctx, networkDeadline)
	defer cancel()
	if path != networkConfig {
		return failedNetworkPath
	}
	// This is an accidental-host-execution guard, not a verity or attestation
	// claim. The image/boot chain must authenticate the complete runtime root.
	var fs syscall.Statfs_t
	if syscall.Statfs("/", &fs) != nil || !networkFilesystem(fs) {
		return failedNetworkFilesystem
	}
	interfaces, err := net.Interfaces()
	if err != nil || len(interfaces) > 32 {
		return failedNetworkInterfaces
	}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagLoopback == 0 && iface.Flags&net.FlagUp != 0 {
			return failedNetworkInterfaces
		}
	}
	raw, err := networkReadFile(networkConfig, 4096, 0o644)
	if err != nil {
		return failedNetworkConfigRead
	}
	return applyNetwork(ctx, raw, func(ctx context.Context, op networkOperation, input []byte) ([]byte, error) {
		executable, err := networkReadFile(nftExecutable, nftSize, 0o755)
		digest := sha256.Sum256(executable)
		if err != nil || len(executable) != nftSize || hex.EncodeToString(digest[:]) != nftSHA256 {
			return nil, failedNetworkExecutable
		}
		var args []string
		switch op {
		case networkRead:
			if input != nil {
				return nil, errNetwork
			}
			// JSON defaults to numeric addresses/ports; --numeric also changes
			// symbolic ct states, and --reversedns/--service are never enabled.
			args = []string{"--json", "list", "ruleset"}
		case networkCheck:
			args = []string{"--check", "--file", "-"}
		case networkCreate:
			args = []string{"--file", "-"}
		default:
			return nil, errNetwork
		}
		if len(input) > nftables.ReadbackLimit {
			return nil, errNetwork
		}
		return executeNetwork(ctx, nftExecutable, args, input)
	})
}

func networkFilesystem(fs syscall.Statfs_t) bool {
	return fs.Type == 0xe0f5e1e2 && fs.Flags&1 != 0 // EROFS_SUPER_MAGIC, ST_RDONLY.
}

// Fixed paths only, descriptor-relative traversal, no symlink or hardlink,
// root ownership, exact modes and stable bounded reads on read-only EROFS.
func networkReadFile(path string, limit int64, mode uint32) ([]byte, error) {
	if path != networkConfig && path != nftExecutable {
		return nil, errNetwork
	}
	fd, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errNetwork
	}
	defer func() { _ = syscall.Close(fd) }()
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for i, part := range parts {
		var parent syscall.Stat_t
		if syscall.Fstat(fd, &parent) != nil || parent.Uid != 0 || parent.Mode&0o022 != 0 {
			return nil, errNetwork
		}
		flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK | syscall.O_CLOEXEC
		if i != len(parts)-1 {
			flags |= syscall.O_DIRECTORY
		}
		next, err := syscall.Openat(fd, part, flags, 0)
		if err != nil {
			return nil, errNetwork
		}
		_ = syscall.Close(fd)
		fd = next
	}
	var before, after syscall.Stat_t
	var fs syscall.Statfs_t
	if syscall.Fstat(fd, &before) != nil || syscall.Fstatfs(fd, &fs) != nil ||
		!networkInput(before, fs, limit, mode) {
		return nil, errNetwork
	}
	dup, err := syscall.Dup(fd)
	if err != nil {
		return nil, errNetwork
	}
	file := os.NewFile(uintptr(dup), "network-input")
	raw, readErr := io.ReadAll(io.LimitReader(file, limit+1))
	closeErr := file.Close()
	if readErr != nil || closeErr != nil || int64(len(raw)) != before.Size ||
		syscall.Fstat(fd, &after) != nil || before != after {
		return nil, errNetwork
	}
	return raw, nil
}

func networkInput(info syscall.Stat_t, fs syscall.Statfs_t, limit int64, mode uint32) bool {
	return info.Mode == syscall.S_IFREG|mode && info.Uid == 0 && info.Gid == 0 && info.Nlink == 1 &&
		info.Size > 0 && info.Size <= limit && networkFilesystem(fs)
}

//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"errors"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

const networkDeadline = 8 * time.Second

var errNetwork = errors.New("network startup rejected")

// Fixed process status only; no backend error, path, policy or tool output.
type networkFailure uint8

const (
	failedNetworkPath networkFailure = 64 + iota
	failedNetworkFilesystem
	failedNetworkInterfaces
	failedNetworkConfigRead
	failedNetworkConfig
	failedNetworkPolicy
	failedNetworkExecutable
	failedNetworkInitialCommand
	failedNetworkInitialRuleset
	failedNetworkCheck
	failedNetworkCreate
	failedNetworkReadbackCommand
	failedNetworkReadback
	failedNetworkDeadline
)

func (f networkFailure) Error() string        { return "network startup rejected" }
func (f networkFailure) serviceExitCode() int { return int(f) }

// Fixed guarded-read substeps. Only the configuration caller forwards these;
// executable reads still collapse to failedNetworkExecutable.
type networkFileFailure uint8

const (
	failedFileRoot networkFileFailure = 78 + iota
	failedFileParentStat
	failedFileParentTrust
	failedFileOpen
	failedFileStat
	failedFileFilesystem
	failedFileMode
	failedFileOwner
	failedFileLinks
	failedFileSize
	failedFileDuplicate
	failedFileRead
	failedFileClose
	failedFileLength
	failedFileRestat
	failedFileChanged
)

func (f networkFileFailure) Error() string        { return "network startup rejected" }
func (f networkFileFailure) serviceExitCode() int { return int(f) }

func networkConfigReadFailure(err error) error {
	if stage, ok := err.(networkFileFailure); ok && stage >= failedFileRoot && stage <= failedFileChanged {
		return stage
	}
	return failedNetworkConfigRead
}

func networkCommandFailure(err error, stage networkFailure) error {
	if err == failedNetworkExecutable {
		return err
	}
	return stage
}

type networkReadbackFailure uint8

func (networkReadbackFailure) Error() string          { return "network startup rejected" }
func (f networkReadbackFailure) serviceExitCode() int { return int(f) }

func networkVerificationFailure(err error) error {
	if stage, ok := err.(nftables.VerificationFailure); ok &&
		stage >= nftables.RejectedPolicy && stage <= nftables.RejectedOther {
		return networkReadbackFailure(94 + stage - nftables.RejectedPolicy)
	}
	return failedNetworkReadback
}

type networkOperation int

const (
	networkRead networkOperation = iota
	networkCheck
	networkCreate
)

// The test seam is package-private. Ordinary dispatch always selects the fixed
// guarded executable; there is no runtime executor/configuration override.
func applyNetwork(ctx context.Context, raw []byte, run func(context.Context, networkOperation, []byte) ([]byte, error)) error {
	ctx, cancel := context.WithTimeout(ctx, networkDeadline)
	defer cancel()
	destinations, err := nftables.Config(raw)
	if err != nil {
		return failedNetworkConfig
	}
	if ctx.Err() != nil {
		return failedNetworkDeadline
	}
	policy, err := nftables.Create(destinations)
	if err != nil {
		return failedNetworkPolicy
	}
	before, err := run(ctx, networkRead, nil)
	if err != nil {
		return networkCommandFailure(err, failedNetworkInitialCommand)
	}
	if nftables.Empty(before) != nil {
		return failedNetworkInitialRuleset
	}
	for _, op := range []networkOperation{networkCheck, networkCreate} {
		out, err := run(ctx, op, policy)
		if err != nil || len(out) != 0 || ctx.Err() != nil {
			stage := failedNetworkCheck
			if op == networkCreate {
				stage = failedNetworkCreate
			}
			return networkCommandFailure(err, stage)
		}
	}
	after, err := run(ctx, networkRead, nil)
	if err != nil {
		return networkCommandFailure(err, failedNetworkReadbackCommand)
	}
	if err := nftables.Verify(after, destinations); err != nil {
		return networkVerificationFailure(err)
	}
	if ctx.Err() != nil {
		return failedNetworkDeadline
	}
	return nil
}

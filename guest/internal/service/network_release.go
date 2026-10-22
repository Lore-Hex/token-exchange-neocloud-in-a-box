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
	if err != nil || ctx.Err() != nil {
		return errNetwork
	}
	policy, err := nftables.Create(destinations)
	if err != nil {
		return errNetwork
	}
	before, err := run(ctx, networkRead, nil)
	if err != nil || nftables.Empty(before) != nil {
		return errNetwork
	}
	for _, op := range []networkOperation{networkCheck, networkCreate} {
		out, err := run(ctx, op, policy)
		if err != nil || len(out) != 0 || ctx.Err() != nil {
			return errNetwork
		}
	}
	after, err := run(ctx, networkRead, nil)
	if err != nil || nftables.Verify(after, destinations) != nil || ctx.Err() != nil {
		return errNetwork
	}
	return nil
}

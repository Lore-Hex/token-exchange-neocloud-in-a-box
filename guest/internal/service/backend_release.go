//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"errors"
	"io"
)

func runBackend(ctx context.Context, role, path string, _ io.Writer) error {
	if role == "network" {
		return runNetwork(ctx, path)
	}
	// GPU, weights and engine remain unavailable; network success cannot
	// appraise hardware, mount weights, start inference or open a listener.
	return errors.New("real guest backends unavailable")
}

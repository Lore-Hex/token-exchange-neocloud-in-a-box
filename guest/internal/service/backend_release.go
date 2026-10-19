//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"context"
	"errors"
	"io"
)

func runBackend(context.Context, string, string, io.Writer) error {
	// Do not read fixtures, execute tools, mount disks, change firewall rules,
	// enable GPU readiness, or open a listener without real implementations.
	return errors.New("real guest backends unavailable")
}

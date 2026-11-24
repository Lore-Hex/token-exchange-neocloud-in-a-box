//go:build !nibfake

// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"errors"
	"io"
)

func run(context.Context, []string, io.Writer) error {
	return errors.New("test collector excluded from release")
}

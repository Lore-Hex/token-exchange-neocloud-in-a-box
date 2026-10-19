//go:build !nibfake

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"errors"
	"io"
)

func run(context.Context, []string, io.Writer) error {
	return errors.New("test collector excluded from release")
}

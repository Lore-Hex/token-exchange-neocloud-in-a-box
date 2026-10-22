//go:build !linux && !nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import "context"

func runNetwork(context.Context, string) error { return errNetwork }

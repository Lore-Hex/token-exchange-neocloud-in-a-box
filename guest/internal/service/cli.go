// SPDX-License-Identifier: Apache-2.0

// Package service is private to guest helpers. It exposes command dispatch, not
// verifier injection or a runtime switch between real and simulated appraisal.
package service

import (
	"context"
	"fmt"
	"io"
)

// Run returns only fixed diagnostics: never echo paths, input, or backend errors.
func Run(ctx context.Context, role string, args []string, out, errOut io.Writer) int {
	switch role {
	case "gpu-gate", "weights", "network", "engine":
	default:
		fmt.Fprintln(errOut, "guest: unsupported service")
		return 2
	}
	if len(args) != 2 || args[0] != "--config" || args[1] == "" {
		fmt.Fprintln(errOut, "usage: guest-helper --config <configuration-file>")
		return 2
	}
	if err := runBackend(ctx, role, args[1], out); err != nil {
		fmt.Fprintln(errOut, "guest: startup denied or service stopped; no confidentiality established")
		if code, ok := err.(interface{ serviceExitCode() int }); ok && role == "network" {
			if status := code.serviceExitCode(); status >= 64 && status <= 77 {
				return status
			}
		}
		return 1
	}
	return 0
}

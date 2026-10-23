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
	return runCommand(ctx, role, args, out, errOut, runBackend)
}

// Package-private dispatch seam for CLI tests. Run always selects runBackend;
// no caller option, mutable executor or runtime override is provided.
func runCommand(ctx context.Context, role string, args []string, out, errOut io.Writer,
	backend func(context.Context, string, string, io.Writer) error) int {
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
	if err := backend(ctx, role, args[1], out); err != nil {
		fmt.Fprintln(errOut, "guest: startup denied or service stopped; no confidentiality established")
		return failureExitStatus(role, err)
	}
	return 0
}

func failureExitStatus(role string, err error) int {
	if code, ok := err.(interface{ serviceExitCode() int }); ok && role == "network" {
		if status := code.serviceExitCode(); status >= 64 && status <= 110 {
			return status
		}
	}
	return 1
}

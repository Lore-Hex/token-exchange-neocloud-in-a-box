// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
)

func runStage(ctx context.Context, args []string, out, errOut io.Writer) int {
	flags := flag.NewFlagSet("stage", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	download := flags.Bool("download", false, "download pinned public weights")
	maxBytes := flags.Int64("max-bytes", 0, "maximum downloaded body bytes")
	timeout := flags.Duration("timeout", 0, "maximum run duration, up to 24h")
	if flags.Parse(args) != nil || (!*download && (flags.NArg() != 1 || *maxBytes != 0 || *timeout != 0)) ||
		(*download && (flags.NArg() != 2 || *maxBytes <= 0 || *timeout <= 0)) {
		fmt.Fprintln(errOut, "usage: nib model stage <pack> | stage --download --max-bytes N --timeout D <pack> <absolute-staged-directory>")
		return 2
	}
	if !*download {
		plan, err := PlanStage(flags.Arg(0))
		if err != nil {
			fmt.Fprintln(errOut, err)
			return 1
		}
		if json.NewEncoder(out).Encode(plan) != nil {
			return 1
		}
		return 0
	}
	ctx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()
	proof, err := Stage(ctx, flags.Arg(0), flags.Arg(1), StageOptions{MaxBytes: *maxBytes, Timeout: *timeout})
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	if json.NewEncoder(out).Encode(proof) != nil {
		return 1
	}
	return 0
}

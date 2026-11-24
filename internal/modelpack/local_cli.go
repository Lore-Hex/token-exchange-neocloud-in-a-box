// SPDX-License-Identifier: AGPL-3.0-or-later

package modelpack

import (
	"context"
	"flag"
	"fmt"
	"io"
)

func runLocal(ctx context.Context, args []string, out, errOut io.Writer) int {
	flags := flag.NewFlagSet("check-local", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	workers := flags.Int("workers", 1, "bounded local hash workers")
	if err := flags.Parse(args); err != nil || flags.NArg() != 2 || *workers < 1 || *workers > MaxLocalWorkers {
		fmt.Fprintln(errOut, "modelpack: invalid local verification arguments")
		return 2
	}
	proof, err := CheckLocalWithWorkers(ctx, flags.Arg(0), flags.Arg(1), *workers)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	if _, err := fmt.Fprintf(out, "Local model bytes match: %d files, %d bytes; no immutable snapshot, deployment or hardware validation.\n", proof.Files, proof.VerifiedBytes); err != nil {
		return 1
	}
	return 0
}

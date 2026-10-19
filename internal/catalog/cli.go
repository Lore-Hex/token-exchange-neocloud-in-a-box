// SPDX-License-Identifier: Apache-2.0

package catalog

import (
	"errors"
	"flag"
	"fmt"
	"io"
)

// Run is also the integration point for the cmd/nib owner: pass args after
// "catalog". No output is written until generation and validation succeed.
func Run(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("nib-catalog", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	root := flags.String("root", ".", "repository root")
	pricing := flags.String("pricing", "catalog/pricing.yaml", "repository-relative pricing file")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Fprintln(stdout, "usage: nib-catalog [--root DIR] [--pricing PATH]\nWrites a synthetic, local-test-only Catalog v2 to stdout. Never publishes.")
			return 0
		}
		fmt.Fprintln(stderr, "nib-catalog: invalid arguments; see --help")
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(stderr, "nib-catalog: unexpected arguments; see --help")
		return 2
	}
	data, err := Generate(*root, *pricing)
	if err != nil {
		fmt.Fprintln(stderr, "nib-catalog:", err)
		return 1
	}
	if n, err := stdout.Write(data); err != nil || n != len(data) {
		fmt.Fprintln(stderr, "nib-catalog: cannot write output")
		return 1
	}
	return 0
}

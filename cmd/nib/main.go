// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"fmt"
	"io"
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/evidence"
	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/modelpack"
)

func run(args []string, out, errOut io.Writer) int {
	return runWithInput(args, os.Stdin, out, errOut)
}

func runWithInput(args []string, in io.Reader, out, errOut io.Writer) int {
	if len(args) == 0 || (len(args) == 1 && (args[0] == "help" || args[0] == "--help")) {
		fmt.Fprintln(out, "nib: Token Exchange Neocloud-in-a-Box (pre-alpha)")
		fmt.Fprintln(out, "No node has been validated on confidential hardware. See PLAN.md.")
		fmt.Fprintln(out, "Commands: evidence new <P0..P9> <slug>; evidence scrub --deny-file <path>")
		fmt.Fprintln(out, "Usage: nib lint [repository-root]")
		fmt.Fprintln(out, "Usage: nib catalog [--root DIR] [--pricing PATH]")
		fmt.Fprintln(out, "Usage: nib model check-remote <pack.yaml-or-directory>")
		return 0
	}
	if args[0] == "lint" {
		if len(args) > 2 {
			fmt.Fprintln(errOut, "usage: nib lint [repository-root]")
			return 2
		}
		root := "."
		if len(args) == 2 {
			root = args[1]
		}
		if err := config.ValidateRepository(root); err != nil {
			fmt.Fprintln(errOut, err)
			return 1
		}
		fmt.Fprintln(out, "Configuration validation passed; no hardware validation performed.")
		return 0
	}
	if args[0] == "evidence" {
		return evidence.Run(args[1:], in, out, errOut)
	}
	if args[0] == "catalog" {
		return catalog.Run(args[1:], out, errOut)
	}
	if args[0] == "model" {
		return modelpack.Run(context.Background(), args[1:], out, errOut)
	}
	// Do not echo arguments: future operator commands may receive sensitive paths.
	fmt.Fprintln(errOut, "unsupported command; run nib --help")
	return 2
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

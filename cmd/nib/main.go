// SPDX-License-Identifier: Apache-2.0
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func run(args []string, out, errOut io.Writer) int {
	if len(args) == 0 || (len(args) == 1 && (args[0] == "help" || args[0] == "--help")) {
		fmt.Fprintln(out, "nib: Token Exchange Neocloud-in-a-Box (pre-alpha)")
		fmt.Fprintln(out, "No node has been validated on confidential hardware. See PLAN.md.")
		fmt.Fprintln(out, "Usage: nib lint [repository-root]")
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
	// Do not echo arguments: future operator commands may receive sensitive paths.
	fmt.Fprintln(errOut, "unsupported command; run nib --help")
	return 2
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

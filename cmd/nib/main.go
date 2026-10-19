// SPDX-License-Identifier: Apache-2.0
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/evidence"
)

func run(args []string, out, errOut io.Writer) int {
	return runWithInput(args, os.Stdin, out, errOut)
}

func runWithInput(args []string, in io.Reader, out, errOut io.Writer) int {
	if len(args) == 0 || (len(args) == 1 && (args[0] == "help" || args[0] == "--help")) {
		fmt.Fprintln(out, "nib: Token Exchange Neocloud-in-a-Box (pre-alpha)")
		fmt.Fprintln(out, "No node has been validated on confidential hardware. See PLAN.md.")
		fmt.Fprintln(out, "Commands: evidence new <P0..P9> <slug>; evidence scrub --deny-file <path>")
		return 0
	}
	if args[0] == "evidence" {
		return evidence.Run(args[1:], in, out, errOut)
	}
	// Do not echo arguments: future operator commands may receive sensitive paths.
	fmt.Fprintln(errOut, "unsupported command; run nib --help")
	return 2
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"fmt"
	"io"
	"os"
)

func run(args []string, out, errOut io.Writer) int {
	if len(args) == 0 || (len(args) == 1 && (args[0] == "help" || args[0] == "--help")) {
		fmt.Fprintln(out, "nib: Token Exchange Neocloud-in-a-Box (pre-alpha)")
		fmt.Fprintln(out, "No node has been validated on confidential hardware. See PLAN.md.")
		return 0
	}
	// Do not echo arguments: future operator commands may receive sensitive paths.
	fmt.Fprintln(errOut, "unsupported command; run nib --help")
	return 2
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

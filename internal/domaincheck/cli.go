// SPDX-License-Identifier: Apache-2.0

package domaincheck

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
)

const usage = "usage: nib-domain-check --domain DOMAIN [--timeout 5s]\n" + assurance + "\nDOMAIN must be a canonical lowercase ASCII DNS name; timeout must be greater than zero and at most 30s.\n"

// Run returns 0 only when both names resolve and output succeeds, 1 on lookup
// or output failure, and 2 on invalid usage. It performs no HTTP requests.
func Run(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	return run(ctx, args, &net.Resolver{PreferGo: true, StrictErrors: true}, stdout, stderr)
}

func run(ctx context.Context, args []string, resolver Resolver, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("nib-domain-check", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	domain := flags.String("domain", "", "domain to check")
	timeout := flags.Duration("timeout", defaultTimeout, "total DNS lookup timeout")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return writeOutput(stdout, stderr, []byte(usage))
		}
		fmt.Fprintln(stderr, "nib-domain-check: invalid arguments; see --help")
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(stderr, "nib-domain-check: unexpected arguments; see --help")
		return 2
	}
	report, err := Check(ctx, resolver, *domain, *timeout)
	if err != nil {
		fmt.Fprintln(stderr, "nib-domain-check:", err)
		return 2
	}
	data, err := json.Marshal(report)
	if err != nil {
		fmt.Fprintln(stderr, "nib-domain-check: cannot encode DNS report")
		return 1
	}
	if code := writeOutput(stdout, stderr, append(data, '\n')); code != 0 {
		return code
	}
	for _, result := range report.Checks {
		if result.Status != resolved {
			return 1
		}
	}
	return 0
}

func writeOutput(stdout, stderr io.Writer, data []byte) int {
	if n, err := stdout.Write(data); err != nil || n != len(data) {
		fmt.Fprintln(stderr, "nib-domain-check: cannot write output")
		return 1
	}
	return 0
}

// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/domaincheck"
)

func main() {
	os.Exit(domaincheck.Run(context.Background(), os.Args[1:], os.Stdout, os.Stderr))
}

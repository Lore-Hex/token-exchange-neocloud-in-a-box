// SPDX-License-Identifier: Apache-2.0

package main

import (
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

func main() { os.Exit(catalog.Run(os.Args[1:], os.Stdout, os.Stderr)) }

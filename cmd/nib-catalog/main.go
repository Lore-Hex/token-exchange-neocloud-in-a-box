// SPDX-License-Identifier: AGPL-3.0-or-later

package main

import (
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/catalog"
)

func main() { os.Exit(catalog.Run(os.Args[1:], os.Stdout, os.Stderr)) }

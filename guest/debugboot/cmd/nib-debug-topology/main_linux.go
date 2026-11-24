//go:build linux && amd64 && nibdebugtopology && !nibfake

// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot"
)

func main() { os.Exit(debugboot.RunTopology()) }

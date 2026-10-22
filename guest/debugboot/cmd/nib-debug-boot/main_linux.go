//go:build linux && amd64 && nibdebugboot

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"os"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot"
)

func main() { os.Exit(debugboot.Run()) }

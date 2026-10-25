//go:build linux && amd64 && nibh100appraisal

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"encoding/json"
	"os"
	"os/signal"
	"syscall"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/appraisal"
)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	result := appraisal.Run(ctx, os.Args[1:])
	cancel()
	if json.NewEncoder(os.Stdout).Encode(result) != nil || result.Result != "pass" {
		os.Exit(1)
	}
}

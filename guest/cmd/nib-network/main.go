// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/internal/service"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	os.Exit(service.Run(ctx, "network", os.Args[1:], os.Stdout, os.Stderr))
}

//go:build linux && amd64 && nibdebugtopology && !nibfake

// SPDX-License-Identifier: Apache-2.0
package debugboot

import (
	"context"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugtopology"
)

// RunTopology is deliberately absent from every existing source profile, unit
// and launcher. It reuses, rather than bypasses, the complete live-root check.
func RunTopology() int {
	if len(os.Args) == 2 {
		switch os.Args[1] {
		case "--topology-child=0":
			return debugtopology.Child(0)
		case "--topology-child=1":
			return debugtopology.Child(1)
		}
	}
	if len(os.Args) != 1 || os.Geteuid() != 0 || os.Getegid() != 0 {
		return 1
	}
	timer := time.AfterFunc(rootHardTimeout, func() { os.Exit(1) })
	defer timer.Stop()
	deadline := time.Now().Add(rootCheckTimeout)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	ctx, interrupt := signal.NotifyContext(ctx, syscall.SIGINT, syscall.SIGTERM)
	defer interrupt()
	var stage failureStage
	var read rootReadObservation
	record := "NIB_DEBUG_TOPOLOGY_V1 ROOT\n"
	if check(ctx, deadline, &stage, &read) == nil {
		record = debugtopology.Run(ctx)
	}
	if ctx.Err() != nil {
		record = "NIB_DEBUG_TOPOLOGY_V1 DEADLINE\n"
	}
	output, err := port()
	if err != nil {
		return 1
	}
	defer output.Close()
	n, err := output.Write([]byte(record))
	if err != nil || n != len(record) || record != "NIB_DEBUG_TOPOLOGY_V1 PASS\n" {
		return 1
	}
	return 0
}

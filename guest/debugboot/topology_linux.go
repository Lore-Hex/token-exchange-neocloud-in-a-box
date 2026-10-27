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

// RunTopology is selected only by the explicit topology debug profile. It
// reuses, rather than bypasses, the complete live-root check.
func RunTopology() int {
	if len(os.Args) == 2 {
		switch os.Args[1] {
		case "--stage-initrd-topology":
			return runTopologyStage()
		case "--network-failure":
			return Run()
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

func runTopologyStage() int {
	if os.Geteuid() != 0 || os.Getegid() != 0 {
		return 1
	}
	checkTimeout, hardTimeout := probeTimeouts(true)
	timer := time.AfterFunc(hardTimeout, func() { os.Exit(1) })
	defer timer.Stop()
	deadline := time.Now().Add(checkTimeout)
	raw, err := readBounded("/proc/cmdline", 4096)
	if err != nil {
		return 1
	}
	if _, err := rootHash(raw); err != nil {
		return 1
	}
	output, err := port()
	for attempts := 0; err != nil && attempts < 40 && time.Now().Before(deadline); attempts++ {
		time.Sleep(100 * time.Millisecond)
		output, err = port()
	}
	if err != nil {
		return 1
	}
	defer output.Close()
	record, result := "NIB_DEBUG_TOPOLOGY_V1 STAGE\n", 1
	if stageExecutable(stageTopologyAt) == nil && time.Now().Before(deadline) {
		record, result = InitrdRecord, 0
	}
	n, err := output.Write([]byte(record))
	if err != nil || n != len(record) {
		return 1
	}
	return result
}

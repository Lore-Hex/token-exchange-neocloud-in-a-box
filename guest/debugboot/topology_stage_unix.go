//go:build (linux || darwin) && nibdebugtopology && !nibfake

// SPDX-License-Identifier: AGPL-3.0-or-later
package debugboot

import (
	"embed"
	"os"
)

//go:embed nib-debug-topology.service
var topologyUnits embed.FS

func stageTopologyAt(run *os.Root, executable []byte) error {
	service, err := topologyUnits.ReadFile("nib-debug-topology.service")
	if err != nil {
		return errRejected
	}
	// The existing deny-all service is still required by the immutable image's
	// debug-boot drop-in. Retain its fixed failure observer and shutdown path.
	return stageServiceAt(run, executable, true, service)
}

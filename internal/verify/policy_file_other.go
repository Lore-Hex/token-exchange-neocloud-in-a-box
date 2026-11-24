//go:build !unix

// SPDX-License-Identifier: AGPL-3.0-or-later
package verify

import (
	"errors"
	"os"
)

func openPolicyFile(string) (*os.File, error) {
	return nil, errors.New("bounded policy file access is not implemented on this platform")
}

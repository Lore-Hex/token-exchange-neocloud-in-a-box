//go:build !unix

// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"errors"
	"os"
)

func openPolicyFile(string) (*os.File, error) {
	return nil, errors.New("bounded policy file access is not implemented on this platform")
}

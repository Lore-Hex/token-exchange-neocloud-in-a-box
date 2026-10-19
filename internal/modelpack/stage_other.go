//go:build !linux && !darwin

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"errors"
	"os"
)

func lockStaging(*os.Root, os.FileInfo) (*os.File, error) {
	return nil, errors.New("modelpack: weight staging requires Linux or macOS")
}

//go:build !linux && !darwin

// SPDX-License-Identifier: AGPL-3.0-or-later

package modelpack

import (
	"errors"
	"os"
)

func lockStaging(*os.Root, os.FileInfo) (*os.File, error) {
	return nil, errors.New("modelpack: weight staging requires Linux or macOS")
}

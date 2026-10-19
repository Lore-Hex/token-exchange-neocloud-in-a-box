//go:build !linux && !darwin

// SPDX-License-Identifier: Apache-2.0

package modelpack

import (
	"errors"
	"os"
)

func openLocalWeight(*os.Root, string) (*os.File, error) {
	return nil, errors.New("modelpack: local verification requires Linux or macOS")
}

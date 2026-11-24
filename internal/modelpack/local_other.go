//go:build !linux && !darwin

// SPDX-License-Identifier: AGPL-3.0-or-later

package modelpack

import (
	"errors"
	"os"
)

func openLocalWeight(*os.Root, string) (*os.File, error) {
	return nil, errors.New("modelpack: local verification requires Linux or macOS")
}

// SPDX-License-Identifier: AGPL-3.0-or-later

// Package schemas contains the operator configuration schemas. It has no
// validator dependencies and must not be used to infer hardware verification.
package schemas

import "embed"

// Files is the authoritative, offline JSON Schema bundle.
//
//go:embed *.schema.json
var Files embed.FS

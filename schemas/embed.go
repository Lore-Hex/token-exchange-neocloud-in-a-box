// SPDX-License-Identifier: Apache-2.0

// Package schemas contains the operator configuration schemas. It has no
// validator dependencies and must not be used to infer hardware verification.
package schemas

import "embed"

// Files is the authoritative, offline JSON Schema bundle.
//
//go:embed *.schema.json
var Files embed.FS

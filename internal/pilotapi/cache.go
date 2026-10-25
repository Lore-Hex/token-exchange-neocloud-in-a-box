// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
)

// The gateway derives this opaque scope from the authenticated workspace, never
// from end-user JSON. This single-key provider API trusts that gateway, not users.
// vLLM hashes cache_salt into the first block and every descendant block.
func selectedCacheSalt(raw json.RawMessage) (json.RawMessage, error) {
	var namespace string
	if len(raw) == 0 {
		var nonce [32]byte
		if _, err := rand.Read(nonce[:]); err != nil {
			return nil, errors.New("cache isolation unavailable")
		}
		namespace = hex.EncodeToString(nonce[:])
	} else {
		if json.Unmarshal(raw, &namespace) != nil || len(namespace) != 64 {
			return nil, errors.New("invalid cache namespace")
		}
		decoded, err := hex.DecodeString(namespace)
		if err != nil || hex.EncodeToString(decoded) != namespace {
			return nil, errors.New("invalid cache namespace")
		}
	}
	salt := sha256.Sum256([]byte("nib-selected-prefix-v1\x00" + namespace))
	return json.Marshal(hex.EncodeToString(salt[:]))
}

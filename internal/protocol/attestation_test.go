// SPDX-License-Identifier: Apache-2.0
package protocol

import (
	"encoding/hex"
	"testing"
)

// Golden vectors from the independently generated public protocol fixtures:
// Lore-Hex/trustedrouter-confidential-stack@a328e98 docs/attestation-vectors.json.
func TestProtocolGoldenVectors(t *testing.T) {
	for _, tc := range []struct{ nonce, spki, data, gpu string }{
		{"0000000000000000000000000000000000000000000000000000000000000000", "0000000000000000000000000000000000000000000000000000000000000000", "0000000000000000000000000000000000000000000000000000000000000000f62f3af470ec975ea41328abbd1c1d0a10be8b908daf0258908cf88302e611d7", "f5008787a4285e8028d13588d31dfc4cc1cd2fa631d46665660bdf2b28520510"},
		{"000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f", "202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3fb7a44c5b9d6abade221bb243e9308e86be8d00429ff3e7670d69a0f6068ff998", "67402eaf309f3ec5a3ea7b113508283982beb4ef3ddf155dfd1e0333faaab2f4"},
		{"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "8080808080808080808080808080808080808080808080808080808080808080", "8080808080808080808080808080808080808080808080808080808080808080194c4540cfb0e059df635febc85f575581a7e49a3367fb7f2d64be32c28f802f", "6731997fd87b68ef91ccb56ad14369d8da8e147e8debf8f0e67f0311258d566e"},
	} {
		n, _ := hex.DecodeString(tc.nonce)
		s, _ := hex.DecodeString(tc.spki)
		data := ReportData([32]byte(n), [32]byte(s))
		gpu := GPUNonce(data)
		if hex.EncodeToString(data[:]) != tc.data || hex.EncodeToString(gpu[:]) != tc.gpu {
			t.Fatal("public transcript vector mismatch")
		}
	}
}

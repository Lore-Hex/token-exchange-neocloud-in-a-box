//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package protocol

// These unmistakably fake byte formats are for GPU-free integration only.
// They are not Intel or NVIDIA reports and do not exist in release builds.
const FakeTDXMagic = "NIBFAKE-TDX-v1\n"
const FakeDeviceFormat = "nibfake-device-v1"

func FakeTDXQuote(data [64]byte, measurements [4][48]byte, flags byte) []byte {
	b := append([]byte(FakeTDXMagic), data[:]...)
	for _, m := range measurements {
		b = append(b, m[:]...)
	}
	return append(b, flags)
}

func FakeDeviceEvidence(nonce [32]byte, kind, id string) []byte {
	b := append([]byte("NIBFAKE-DEVICE-v1\n"), nonce[:]...)
	b = append(b, kind...)
	b = append(b, 0)
	return append(b, id...)
}

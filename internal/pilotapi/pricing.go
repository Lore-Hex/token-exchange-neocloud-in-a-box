// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"errors"
	"math/big"
)

// Pricing declares USD per million tokens. Nil CachedInput preserves the
// profile's existing tariff; an explicit zero is not the same as omission.
type Pricing struct {
	Input       string
	Output      string
	CachedInput *string
}

func (p Pricing) cachedInput(profile serviceProfile) (any, error) {
	if !pricePattern.MatchString(p.Input) || !pricePattern.MatchString(p.Output) {
		return nil, errors.New("explicit decimal input and output prices required")
	}
	if p.CachedInput != nil {
		if profile.context != 1048576 || !pricePattern.MatchString(*p.CachedInput) {
			return nil, errors.New("cache price requires a decimal and the million profile")
		}
		cached, _ := new(big.Rat).SetString(*p.CachedInput)
		input, _ := new(big.Rat).SetString(p.Input)
		if cached.Cmp(input) > 0 {
			return nil, errors.New("cache price must not exceed input price")
		}
		return *p.CachedInput, nil
	}
	if profile.context == 1048576 {
		return p.Input, nil
	}
	return nil, nil
}

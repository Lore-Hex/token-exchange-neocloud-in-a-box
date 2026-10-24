// SPDX-License-Identifier: Apache-2.0
package pilotapi

import "errors"

const ProfileQwen = "qwen"
const ProfileSelectedDeepSeek = "selected-deepseek"

// Profiles are closed startup choices, not caller-supplied model aliases or
// backend settings. The same value binds catalog, admission and request bounds.
type serviceProfile struct {
	model, name                    string
	context, maxOutput, concurrent int
	reasoningHints                 bool
}

func qwenProfile() serviceProfile {
	return serviceProfile{model: Model, name: "Qwen3 0.6B (CPU pilot)",
		context: ContextLength, maxOutput: MaxOutput, concurrent: 2, reasoningHints: true}
}

func profileFor(name string) (serviceProfile, error) {
	switch name {
	case ProfileQwen:
		return qwenProfile(), nil
	case ProfileSelectedDeepSeek:
		// The pack's tr_model_id, not its bare lab served_model_name. A future
		// owned engine must serve this exact ID; this API never rewrites it.
		return serviceProfile{model: "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8",
			name:    "DeepSeek V4.1 Flash 1017 UNCENSORED FP8 (selected)",
			context: 8192, maxOutput: 256, concurrent: 1}, nil
	default:
		return serviceProfile{}, errors.New("unsupported API profile")
	}
}

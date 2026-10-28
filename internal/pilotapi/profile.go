// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"errors"
	"time"
)

const ProfileQwen = "qwen"
const ProfileSelectedDeepSeek = "selected-deepseek"
const ProfileSelectedGraphs = "selected-deepseek-graphs"
const ProfileSelectedQuality = "selected-deepseek-quality"
const ProfileSelectedLong = "selected-deepseek-long"
const ProfileSelectedMillion = "selected-deepseek-million"
const ProfileOriginalMillion = "original-deepseek-million"

// Profiles are closed startup choices, not caller-supplied model aliases or
// backend settings. The same value binds catalog, admission and request bounds.
type serviceProfile struct {
	model, name                    string
	context, maxOutput, concurrent int
	reasoningHints                 bool
	nativeReasoning                bool
	nativeTools                    bool
	longContext                    bool
}

func (p serviceProfile) bodyLimit() int {
	if p.context == 1048576 {
		return 16 << 20
	}
	if p.longContext {
		return 4 << 20
	}
	return 65536
}

func (p serviceProfile) requestTimeout() time.Duration {
	if p.context == 1048576 {
		return 3540 * time.Second
	}
	if p.longContext {
		return 900 * time.Second
	}
	return 120 * time.Second
}

func (p serviceProfile) defaultOutput() int {
	if p.longContext {
		return 2048 // A larger ceiling must not silently expand an unspecified request.
	}
	return p.maxOutput
}

func qwenProfile() serviceProfile {
	return serviceProfile{model: Model, name: "Qwen3 0.6B (CPU pilot)",
		context: ContextLength, maxOutput: MaxOutput, concurrent: 2, reasoningHints: true}
}

func profileFor(name string) (serviceProfile, error) {
	switch name {
	case ProfileQwen:
		return qwenProfile(), nil
	case ProfileSelectedDeepSeek, ProfileSelectedGraphs, ProfileSelectedQuality, ProfileSelectedLong, ProfileSelectedMillion, ProfileOriginalMillion:
		// The pack's tr_model_id, not its bare lab served_model_name. A future
		// owned engine must serve this exact ID; this API never rewrites it.
		profile := serviceProfile{model: "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8",
			name:    "DeepSeek V4.1 Flash 1017 UNCENSORED FP8 (selected)",
			context: 8192, maxOutput: 256, concurrent: 1, nativeReasoning: true, nativeTools: true}
		if name == ProfileSelectedGraphs {
			// Eight total admissions, not eight for each of the two DP ranks.
			profile.context, profile.concurrent = 4096, 8
		}
		if name == ProfileSelectedQuality {
			profile.context, profile.maxOutput = 32768, 2048
		}
		if name == ProfileSelectedLong {
			profile.context, profile.maxOutput, profile.longContext = 131072, 16384, true
		}
		if name == ProfileSelectedMillion || name == ProfileOriginalMillion {
			profile.context, profile.maxOutput, profile.longContext = 1048576, 393216, true
		}
		if name == ProfileOriginalMillion {
			// Separate identity from models/deepseek-v4.1-flash-1017/1/pack.yaml;
			// never reinterpret the existing selected checkpoint's model ID.
			profile.model = "deepseek/deepseek-v4.1-flash-1017"
			profile.name = "DeepSeek V4.1 Flash 1017"
		}
		return profile, nil
	default:
		return serviceProfile{}, errors.New("unsupported API profile")
	}
}

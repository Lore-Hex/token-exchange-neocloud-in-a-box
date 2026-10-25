// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"errors"
)

// Pinned vLLM 5df63a7 forwards reasoning_effort into the DeepSeek V4.1
// tokenizer; none disables thinking. Source and equivalences are documented
// in docs/tasks/p2-t4-selected-reasoning-controls.md.
func selectedEffort(effort string) bool {
	switch effort {
	case "none", "low", "high", "xhigh", "max":
		return true
	default:
		return false
	}
}

func normalizeSelectedReasoning(value map[string]json.RawMessage) error {
	invalid := errors.New("unsupported selected reasoning option")
	field := ""
	for _, name := range []string{"thinking", "reasoning", "reasoning_effort"} {
		if _, ok := value[name]; ok {
			if field != "" {
				return invalid
			}
			field = name
		}
	}
	if field == "" {
		return nil
	}
	var effort string
	if field == "reasoning_effort" {
		if json.Unmarshal(value[field], &effort) != nil {
			return invalid
		}
	} else {
		var options map[string]string
		if json.Unmarshal(value[field], &options) != nil || len(options) != 1 {
			return invalid
		}
		if field == "thinking" {
			if options["type"] != "disabled" {
				return invalid
			}
			effort = "none"
		} else {
			effort = options["effort"]
		}
	}
	if !selectedEffort(effort) {
		return invalid
	}
	delete(value, field)
	value["reasoning_effort"], _ = json.Marshal(effort)
	return nil
}

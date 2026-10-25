// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"unicode/utf8"
)

var errSelectedTools = errors.New("unsupported selected tool request")
var toolName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_-]{0,63}$`)
var toolID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

// The pinned DeepSeek V4.1 parser/encoder already supports these native fields.
// No tool is executed here. See docs/tasks/p2-t4-selected-service-tools.md.
func validateSelectedTools(value map[string]json.RawMessage) error {
	names := map[string]bool{}
	if raw, present := value["tools"]; present {
		var tools []map[string]json.RawMessage
		if json.Unmarshal(raw, &tools) != nil || len(tools) == 0 || len(tools) > 16 {
			return errSelectedTools
		}
		for _, tool := range tools {
			var function, parameters map[string]json.RawMessage
			if len(tool) != 2 || toolText(tool["type"]) != "function" ||
				json.Unmarshal(tool["function"], &function) != nil ||
				!toolFields(function, "name", "parameters", "description") ||
				json.Unmarshal(function["parameters"], &parameters) != nil ||
				toolText(parameters["type"]) != "object" {
				return errSelectedTools
			}
			name := toolText(function["name"])
			if !toolName.MatchString(name) || names[name] {
				return errSelectedTools
			}
			if raw, present := function["description"]; present && !toolString(raw, 4096) {
				return errSelectedTools
			}
			names[name] = true
		}
	}
	if raw, present := value["tool_choice"]; present {
		if choice := toolText(raw); len(names) == 0 || (choice != "auto" && choice != "none") {
			return errSelectedTools
		}
	}
	if raw, present := value["parallel_tool_calls"]; present {
		var parallel bool
		if len(names) == 0 || bytes.Equal(raw, []byte("null")) || json.Unmarshal(raw, &parallel) != nil || parallel {
			return errSelectedTools
		}
	}
	var messages []map[string]json.RawMessage
	if json.Unmarshal(value["messages"], &messages) != nil || len(messages) == 0 || len(messages) > 128 {
		return errSelectedTools
	}
	seen, pending := map[string]bool{}, map[string]bool{}
	for _, message := range messages {
		role := toolText(message["role"])
		if len(pending) > 0 && role != "tool" {
			return errSelectedTools
		}
		switch role {
		case "system", "user":
			if len(message) != 2 || !toolString(message["content"], 65536) {
				return errSelectedTools
			}
		case "tool":
			id := toolText(message["tool_call_id"])
			if len(message) != 3 || !pending[id] || !toolString(message["content"], 65536) {
				return errSelectedTools
			}
			delete(pending, id)
		case "assistant":
			if !toolFields(message, "role", "content", "tool_calls", "reasoning") {
				return errSelectedTools
			}
			if raw, present := message["reasoning"]; present && !toolString(raw, 32768) {
				return errSelectedTools
			}
			callsRaw, hasCalls := message["tool_calls"]
			if !toolString(message["content"], 65536) && !(hasCalls && bytes.Equal(message["content"], []byte("null"))) {
				return errSelectedTools
			}
			if !hasCalls {
				continue
			}
			var calls []map[string]json.RawMessage
			if json.Unmarshal(callsRaw, &calls) != nil || len(calls) == 0 || len(calls) > 16 {
				return errSelectedTools
			}
			for _, call := range calls {
				var function map[string]json.RawMessage
				id := toolText(call["id"])
				if len(call) != 3 || !toolID.MatchString(id) || seen[id] || toolText(call["type"]) != "function" ||
					json.Unmarshal(call["function"], &function) != nil || len(function) != 2 ||
					!names[toolText(function["name"])] || !toolString(function["arguments"], 32768) {
					return errSelectedTools
				}
				arguments := []byte(toolText(function["arguments"]))
				var object map[string]json.RawMessage
				if !toolJSON(arguments) || json.Unmarshal(arguments, &object) != nil || object == nil {
					return errSelectedTools
				}
				seen[id], pending[id] = true, true
			}
		default:
			return errSelectedTools
		}
	}
	if len(pending) != 0 {
		return errSelectedTools
	}
	return nil
}

func toolFields(object map[string]json.RawMessage, allowed ...string) bool {
	for key := range object {
		found := false
		for _, field := range allowed {
			found = found || field == key
		}
		if !found {
			return false
		}
	}
	return true
}

func toolString(raw json.RawMessage, limit int) bool {
	var value string
	return len(raw) != 0 && !bytes.Equal(raw, []byte("null")) &&
		json.Unmarshal(raw, &value) == nil && len(value) <= limit
}

func toolText(raw json.RawMessage) string {
	var value string
	_ = json.Unmarshal(raw, &value)
	return value
}

// Reuse encoding/json, but reject duplicate keys and excessive nesting before
// forwarding opaque schema/history data. Argument strings get the same check.
func toolJSON(raw []byte) bool {
	if len(raw) == 0 || len(raw) > 65536 || !utf8.Valid(raw) {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var visit func(int) bool
	visit = func(depth int) bool {
		if depth > 32 {
			return false
		}
		token, err := decoder.Token()
		if err != nil {
			return false
		}
		switch token {
		case json.Delim('{'):
			keys := map[string]bool{}
			for decoder.More() {
				token, err := decoder.Token()
				key, ok := token.(string)
				if err != nil || !ok || keys[key] || !visit(depth+1) {
					return false
				}
				keys[key] = true
			}
			token, err = decoder.Token()
			return err == nil && token == json.Delim('}')
		case json.Delim('['):
			for decoder.More() {
				if !visit(depth + 1) {
					return false
				}
			}
			token, err = decoder.Token()
			return err == nil && token == json.Delim(']')
		}
		_, delimiter := token.(json.Delim)
		if number, ok := token.(json.Number); ok {
			_, err := number.Float64()
			return err == nil
		}
		return !delimiter
	}
	if !visit(0) {
		return false
	}
	_, err := decoder.Token()
	return err == io.EOF
}

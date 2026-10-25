// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

const toolDeclaration = `{"type":"function","function":{"name":"nib_echo","description":"Return the supplied synthetic value.","parameters":{"type":"object","properties":{"value":{"type":"string"}},"required":["value"],"additionalProperties":false}}}`
const toolCall = `{"id":"call_synthetic_1","type":"function","function":{"name":"nib_echo","arguments":"{\"value\":\"PONG\"}"}}`
const toolHistory = `[{"role":"user","content":"Call nib_echo with value PONG."},{"role":"assistant","content":null,"reasoning":"synthetic reasoning, separate from arguments","tool_calls":[` + toolCall + `]},{"role":"tool","tool_call_id":"call_synthetic_1","content":"PONG"}]`

func toolRequest(messages string) string {
	return `{"model":"` + selectedModel + `","messages":` + messages + `,"tools":[` + toolDeclaration + `],"tool_choice":"auto","parallel_tool_calls":false,"reasoning_effort":"low","temperature":0,"max_tokens":256}`
}

func TestSelectedToolRequestAndHistoryPreserved(t *testing.T) {
	h := selectedHandler(t)
	for _, messages := range []string{`[{"role":"user","content":"Call nib_echo with value PONG."}]`, toolHistory} {
		for _, choice := range []string{"auto", "none"} {
			body := strings.Replace(toolRequest(messages), `"tool_choice":"auto"`, `"tool_choice":"`+choice+`"`, 1)
			bounded, err := h.profile.boundRequest([]byte(body))
			if err != nil {
				t.Fatal(err)
			}
			var original, forwarded map[string]any
			if json.Unmarshal([]byte(body), &original) != nil || json.Unmarshal(bounded, &forwarded) != nil || !reflect.DeepEqual(original, forwarded) {
				t.Fatal("tool fields, argument string, reasoning or limits were changed")
			}
		}
	}
	body := strings.Replace(toolRequest(toolHistory), `,"tool_choice":"auto","parallel_tool_calls":false`, "", 1)
	bounded, err := h.profile.boundRequest([]byte(body))
	if err != nil || strings.Contains(string(bounded), "tool_choice") || strings.Contains(string(bounded), "parallel_tool_calls") {
		t.Fatal("absent tool controls must preserve native defaults")
	}
	// Results may arrive in a different order, but every ID is joined once.
	second := strings.ReplaceAll(toolCall, "call_synthetic_1", "call_synthetic_2")
	body = strings.Replace(toolRequest(toolHistory), `"tool_calls":[`+toolCall+`]`, `"tool_calls":[`+toolCall+`,`+second+`]`, 1)
	body = strings.Replace(body, `{"role":"tool","tool_call_id":"call_synthetic_1"`, `{"role":"tool","tool_call_id":"call_synthetic_2","content":"PONG"},{"role":"tool","tool_call_id":"call_synthetic_1"`, 1)
	if _, err := h.profile.boundRequest([]byte(body)); err != nil {
		t.Fatal("matched multi-call history rejected")
	}
}

func TestSelectedToolMalformedFieldsRejected(t *testing.T) {
	h := selectedHandler(t)
	base := toolRequest(toolHistory)
	for _, tc := range []struct{ name, from, to string }{
		{"empty tools", `[` + toolDeclaration + `]`, `[]`},
		{"null tools", `[` + toolDeclaration + `]`, `null`},
		{"duplicate declarations", `[` + toolDeclaration + `]`, `[` + toolDeclaration + `,` + toolDeclaration + `]`},
		{"too many declarations", `[` + toolDeclaration + `]`, `[` + strings.Repeat(toolDeclaration+`,`, 16) + toolDeclaration + `]`},
		{"tool type", `"type":"function"`, `"type":"custom"`},
		{"invalid name", `"name":"nib_echo"`, `"name":"private.name"`},
		{"strict mode", `"name":"nib_echo","description"`, `"name":"nib_echo","strict":true,"description"`},
		{"description type", `"description":"Return the supplied synthetic value."`, `"description":null`},
		{"long description", `"description":"Return the supplied synthetic value."`, `"description":"` + strings.Repeat("x", 4097) + `"`},
		{"parameters null", `"parameters":{"type":"object"`, `"parameters":{"type":null`},
		{"parameters array", `"parameters":{"type":"object"`, `"parameters":{"type":"array"`},
		{"required choice", `"tool_choice":"auto"`, `"tool_choice":"required"`},
		{"named choice", `"tool_choice":"auto"`, `"tool_choice":{"type":"function","function":{"name":"nib_echo"}}`},
		{"null choice", `"tool_choice":"auto"`, `"tool_choice":null`},
		{"parallel true", `"parallel_tool_calls":false`, `"parallel_tool_calls":true`},
		{"parallel null", `"parallel_tool_calls":false`, `"parallel_tool_calls":null`},
		{"unknown field", `"tool_choice":"auto"`, `"tool_choice":"auto","chat_template_kwargs":{}`},
		{"excess output", `"max_tokens":256`, `"max_tokens":257`},
		{"multiple completions", `"max_tokens":256`, `"max_tokens":256,"n":2`},
		{"unknown function", `"function":{"name":"nib_echo","arguments"`, `"function":{"name":"other","arguments"`},
		{"missing call id", `"id":"call_synthetic_1",`, ``},
		{"empty call id", `"id":"call_synthetic_1"`, `"id":""`},
		{"long call id", `"id":"call_synthetic_1"`, `"id":"` + strings.Repeat("x", 129) + `"`},
		{"extra call field", `"id":"call_synthetic_1"`, `"id":"call_synthetic_1","name":"nib_echo"`},
		{"null calls", `"tool_calls":[` + toolCall + `]`, `"tool_calls":null`},
		{"empty calls", `"tool_calls":[` + toolCall + `]`, `"tool_calls":[]`},
		{"duplicate calls", `"tool_calls":[` + toolCall + `]`, `"tool_calls":[` + toolCall + `,` + toolCall + `]`},
		{"nonstring args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":{"value":"PONG"}`},
		{"malformed args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"{broken"`},
		{"array args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"[]"`},
		{"null args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"null"`},
		{"double encoded args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"\"{}\""`},
		{"duplicate args keys", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"{\"value\":1,\"value\":2}"`},
		{"nonfinite parsed args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"{\"value\":1e9999}"`},
		{"long args", `"arguments":"{\"value\":\"PONG\"}"`, `"arguments":"{\"value\":\"` + strings.Repeat("x", 32768) + `\"}"`},
		{"unmatched result", `"tool_call_id":"call_synthetic_1"`, `"tool_call_id":"call_other"`},
		{"null result", `"tool_call_id":"call_synthetic_1","content":"PONG"`, `"tool_call_id":"call_synthetic_1","content":null`},
		{"extra result", `"tool_call_id":"call_synthetic_1"`, `"tool_call_id":"call_synthetic_1","name":"nib_echo"`},
		{"reasoning alias", `"reasoning":"`, `"reasoning_content":"`},
		{"reasoning null", `"reasoning":"synthetic reasoning, separate from arguments"`, `"reasoning":null`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := strings.Replace(base, tc.from, tc.to, 1)
			if body == base {
				t.Fatal("negative control did not change fixture")
			}
			if _, err := h.profile.boundRequest([]byte(body)); err == nil {
				t.Fatal("malformed/unsupported request accepted")
			}
		})
	}
}

func TestSelectedToolHistoryMustJoinBeforeNextTurn(t *testing.T) {
	h := selectedHandler(t)
	call := `{"role":"assistant","content":null,"tool_calls":[` + toolCall + `]}`
	result := `{"role":"tool","tool_call_id":"call_synthetic_1","content":"PONG"}`
	for _, messages := range []string{
		`[` + call + `]`, `[` + result + `]`, `[` + call + `,` + result + `,` + result + `]`,
		`[` + call + `,{"role":"user","content":"interrupt"},` + result + `]`,
		`[` + call + `,` + result + `,` + call + `,` + result + `]`,
		`[{"role":"assistant","content":null}]`,
		`[{"role":"user","content":"PONG","reasoning":"private canary"}]`,
		`[{"role":"system","content":"PONG","tools":[]}]`,
	} {
		if _, err := h.profile.boundRequest([]byte(toolRequest(messages))); err == nil {
			t.Fatal("invalid conversation join accepted")
		}
	}
	for _, body := range []string{
		strings.Replace(toolRequest(toolHistory), `,"tools":[`+toolDeclaration+`]`, "", 1),
		selectedRequest(`,"parallel_tool_calls":false`),
		selectedRequest(`,"tool_choice":"none"`),
	} {
		if _, err := h.profile.boundRequest([]byte(body)); err == nil {
			t.Fatal("undeclared tool controls accepted")
		}
	}
}

func TestSelectedToolJSONBoundsAndDuplicateKeys(t *testing.T) {
	h := selectedHandler(t)
	for _, body := range []string{
		strings.Replace(toolRequest(toolHistory), `"max_tokens":256`, `"max_tokens":256,"max_tokens":1`, 1),
		strings.Replace(toolRequest(toolHistory), `"id":"call_synthetic_1"`, `"id":"call_synthetic_1","\u0069d":"call_other"`, 1),
		strings.Replace(toolRequest(toolHistory), `"properties":{`, `"extra":`+strings.Repeat("[", 33)+`0`+strings.Repeat("]", 33)+`,"properties":{`, 1),
		toolRequest(toolHistory) + `{}`,
		selectedRequest(`,"stop":"` + strings.Repeat("x", 65536) + `"`),
		strings.Replace(toolRequest(toolHistory), "PONG", string([]byte{0xff}), 1),
	} {
		if _, err := h.profile.boundRequest([]byte(body)); err == nil {
			t.Fatal("ambiguous, deeply nested or oversized JSON accepted")
		}
	}
	tooManyMessages := `[` + strings.Repeat(`{"role":"user","content":"PONG"},`, 128) + `{"role":"user","content":"PONG"}]`
	if _, err := h.profile.boundRequest([]byte(toolRequest(tooManyMessages))); err == nil {
		t.Fatal("message bound changed")
	}
	for _, raw := range []string{`{}`, `{"nested":[true,null,1.25,"text",{}]}`} {
		if !toolJSON([]byte(raw)) {
			t.Fatal("valid bounded JSON rejected")
		}
	}
}

func TestSelectedToolResponseErrorsAreNotRepaired(t *testing.T) {
	for _, reply := range []string{
		`{"choices":[{"finish_reason":"tool_calls","message":{"tool_calls":[{"function":{"arguments":"{broken"}}]}}],"usage":{"prompt_tokens":1,"completion_tokens":256,"total_tokens":257}}`,
		`{"choices":[{"finish_reason":"length"}],"usage":{"prompt_tokens":1,"completion_tokens":256,"total_tokens":257}}`,
	} {
		h := selectedHandler(t)
		h.proxy.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/json"}}, Body: io.NopCloser(strings.NewReader(reply))}, nil
		})
		r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(toolRequest(`[{"role":"user","content":"PONG"}]`)))
		r.Header.Set("Authorization", "Bearer "+testKey)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 || w.Body.String() != reply {
			t.Fatal("API rewrote an unqualified model response")
		}
	}
}

func TestSelectedToolsUseExistingAuthenticatedTransport(t *testing.T) {
	for _, stream := range []bool{false, true} {
		h := selectedHandler(t)
		calls := 0
		jsonReply := `{"choices":[{"index":0,"message":{"role":"assistant","content":null,"reasoning":"separate synthetic reasoning","tool_calls":[` + toolCall + `]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":30,"completion_tokens":12,"total_tokens":42}}`
		streamReply := "data: {\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_synthetic_1\",\"type\":\"function\",\"function\":{\"name\":\"nib_echo\",\"arguments\":\"{\\\"value\\\":\"}}]},\"finish_reason\":null}]}\n\n" +
			"data: {\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"PONG\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\n" +
			"data: {\"choices\":[],\"usage\":{\"prompt_tokens\":30,\"completion_tokens\":12,\"total_tokens\":42}}\n\ndata: [DONE]\n\n"
		responses := []string{jsonReply, `{"choices":[{"index":0,"message":{"role":"assistant","content":"PONG"},"finish_reason":"stop"}],"usage":{"prompt_tokens":44,"completion_tokens":2,"total_tokens":46}}`}
		if stream {
			responses[0] = streamReply
		}
		h.proxy.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			if r.URL.String() != "http://127.0.0.1:8000/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+testKey {
				t.Fatal("owned destination or existing auth changed")
			}
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Fatal(err)
			}
			var got map[string]json.RawMessage
			if json.Unmarshal(body, &got) != nil || toolText(got["model"]) != selectedModel || string(got["max_tokens"]) != "256" {
				t.Fatal("identity/output bound changed")
			}
			if calls == 1 {
				var history, want any
				if json.Unmarshal(got["messages"], &history) != nil || json.Unmarshal([]byte(toolHistory), &want) != nil || !reflect.DeepEqual(history, want) {
					t.Fatal("tool continuation/history was rewritten")
				}
			}
			header := http.Header{"Content-Type": {"application/json"}}
			if stream && calls == 0 {
				header.Set("Content-Type", "text/event-stream")
			}
			reply := responses[calls]
			calls++
			return &http.Response{StatusCode: 200, Header: header, Body: io.NopCloser(strings.NewReader(reply))}, nil
		})
		for index, messages := range []string{`[{"role":"user","content":"Call nib_echo with value PONG."}]`, toolHistory} {
			body := toolRequest(messages)
			if stream && index == 0 {
				body = strings.TrimSuffix(body, "}") + `,"stream":true,"stream_options":{"include_usage":true}}`
			}
			for _, key := range []string{"", "wrong", testKey} {
				r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body))
				r.Header.Set("Authorization", "Bearer "+key)
				w := httptest.NewRecorder()
				h.ServeHTTP(w, r)
				if key != testKey {
					if w.Code != 401 || calls != index {
						t.Fatal("tool request bypassed authentication")
					}
				} else if w.Code != 200 || calls != index+1 || w.Body.String() != responses[index] {
					t.Fatal("JSON/stream response, finish or usage bytes were rewritten")
				}
			}
		}
	}
}

func TestSelectedToolErrorsAreContentFreeAndQwenStillRejects(t *testing.T) {
	bad := strings.Replace(toolRequest(toolHistory), `"tool_choice":"auto"`, `"tool_choice":"PRIVATE_SYNTHETIC_CANARY"`, 1)
	h := selectedHandler(t)
	h.proxy.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("invalid input reached engine")
		return nil, nil
	})
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(bad))
	r.Header.Set("Authorization", "Bearer "+testKey)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 400 || strings.Contains(w.Body.String(), "PRIVATE") || strings.Contains(w.Body.String(), "nib_echo") {
		t.Fatal("unsafe/missing error")
	}
	for _, body := range []string{toolRequest(toolHistory), toolRequest(`[{"role":"user","content":"PONG"}]`)} {
		if _, err := BoundRequest([]byte(strings.ReplaceAll(body, selectedModel, Model))); err == nil {
			t.Fatal("Qwen unexpectedly accepts tool fields/history")
		}
	}
}

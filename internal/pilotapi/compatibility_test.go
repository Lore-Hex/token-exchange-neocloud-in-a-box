// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"testing"
	"time"
)

// Captured against the pre-profile API at cf169dd2. These are wire bytes, not
// a comparison between two entry points sharing the same implementation.
const qwenCatalog = `{"contract_version":"2.0","data":[{"capabilities":{"prompt_caching":false,"reasoning":false,"streaming":true,"structured_output":false,"tools":false},"context_length":4096,"endpoints":["chat/completions"],"id":"qwen/qwen3-0.6b","input_modalities":["text"],"lifecycle":{"deprecation_at":null,"replacement_model_id":null,"retirement_at":null,"status":"active"},"max_output_tokens":256,"name":"Qwen3 0.6B (CPU pilot)","object":"model","output_modalities":["text"],"owned_by":"qwen","pricing":{"cache_write":null,"cached_input":null,"currency":"USD","input":"0.10","minimum_request":"0","output":"0.20","unit":"per_1m_tokens"},"reliability":{"capacity_scope":"model","completion_timeout_seconds":120,"first_token_timeout_seconds":30,"stream_idle_timeout_seconds":30},"type":"chat"}],"object":"list","provider":{"error_contract":{"account_quota_error_codes":[],"overload_status":503,"rate_limit_status":429,"retry_after_header":"Retry-After"},"id":"pilot","incident_contact":"mailto:ops@example.invalid","regions":["local-test-only"],"request_id_header":"X-Request-Id","status_url":"https://example.invalid/healthz","support_contact":"mailto:support@example.invalid"}}`

func TestLegacyQwenWireBytes(t *testing.T) {
	data, err := Catalog(testProvider(), "0.10", "0.20", time.Time{})
	if err != nil || string(data) != qwenCatalog {
		t.Fatalf("legacy catalog changed: %v\n%s", err, data)
	}
	for _, tc := range []struct{ input, want string }{
		{requestBody, `{"max_tokens":256,"messages":[{"role":"user","content":"PONG"}],"model":"qwen/qwen3-0.6b"}`},
		{`{"model":"qwen/qwen3-0.6b","messages":[{"role":"user","content":"PONG"}],"max_tokens":16,"max_completion_tokens":8,"thinking":{"type":"disabled"},"reasoning_effort":"low","stream":true,"stream_options":{"include_usage":true}}`,
			`{"max_tokens":8,"messages":[{"role":"user","content":"PONG"}],"model":"qwen/qwen3-0.6b","stream":true,"stream_options":{"include_usage":true}}`},
	} {
		got, err := BoundRequest([]byte(tc.input))
		if err != nil || string(got) != tc.want {
			t.Fatalf("legacy request changed: %v\n%s", err, got)
		}
	}
}

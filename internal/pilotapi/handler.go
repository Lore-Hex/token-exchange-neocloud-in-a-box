// SPDX-License-Identifier: Apache-2.0

// Package pilotapi bounds non-confidential integration API profiles. It is
// not part of a confidential release image or an attestation implementation.
package pilotapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const Model = "qwen/qwen3-0.6b"
const MaxOutput = 256
const ContextLength = 4096

var pricePattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$`)
var apiKeyPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{64,256}$`)

// Catalog requires explicit prices; absent or malformed prices never become free.
func Catalog(provider ProviderConfig, inputPrice, outputPrice string, retirement time.Time) ([]byte, error) {
	return qwenProfile().catalog(provider, inputPrice, outputPrice, retirement)
}

func (p serviceProfile) catalog(provider ProviderConfig, inputPrice, outputPrice string, retirement time.Time) ([]byte, error) {
	if err := provider.Validate(); err != nil {
		return nil, err
	}
	if !pricePattern.MatchString(inputPrice) || !pricePattern.MatchString(outputPrice) {
		return nil, errors.New("explicit decimal input and output prices required")
	}
	var retirementAt any
	if !retirement.IsZero() {
		retirementAt = retirement.UTC().Format(time.RFC3339)
	}
	owner, _, _ := strings.Cut(p.model, "/")
	return json.Marshal(map[string]any{
		"object": "list", "contract_version": "2.0",
		"provider": map[string]any{
			"id": provider.ID, "status_url": provider.StatusURL,
			"support_contact": provider.SupportContact, "incident_contact": provider.IncidentContact,
			"regions": provider.Regions, "request_id_header": "X-Request-Id",
			"error_contract": map[string]any{"rate_limit_status": 429, "overload_status": 503,
				"retry_after_header": "Retry-After", "account_quota_error_codes": []string{}},
		},
		"data": []any{map[string]any{
			"id": p.model, "object": "model", "owned_by": owner, "name": p.name,
			"type": "chat", "context_length": p.context, "max_output_tokens": p.maxOutput,
			"endpoints": []string{"chat/completions"}, "input_modalities": []string{"text"}, "output_modalities": []string{"text"},
			"capabilities": map[string]bool{"streaming": true, "tools": false, "structured_output": false, "reasoning": false, "prompt_caching": false},
			"pricing": map[string]any{"currency": "USD", "unit": "per_1m_tokens", "input": inputPrice,
				"output": outputPrice, "cached_input": nil, "cache_write": nil, "minimum_request": "0"},
			"lifecycle": map[string]any{"status": "active", "deprecation_at": nil, "retirement_at": retirementAt, "replacement_model_id": nil},
			"reliability": map[string]any{"first_token_timeout_seconds": 30, "completion_timeout_seconds": 120,
				"stream_idle_timeout_seconds": 30, "capacity_scope": "model"},
		}},
	})
}

type Handler struct {
	profile    serviceProfile
	key        [32]byte
	catalog    []byte
	proxy      *httputil.ReverseProxy
	slots      chan struct{}
	retirement time.Time
	now        func() time.Time
}

// New fixes the backend to loopback. Neither requests nor environment variables
// can supply an alternate destination for provider credentials or prompt bytes.
func New(provider ProviderConfig, key, inputPrice, outputPrice, retirementAt string) (*Handler, error) {
	return NewWithProfile(ProfileQwen, provider, key, inputPrice, outputPrice, retirementAt)
}

// NewWithProfile selects one closed profile at startup. It keeps New's fixed
// loopback backend and requires the engine to serve the catalog ID unchanged.
func NewWithProfile(profileName string, provider ProviderConfig, key, inputPrice, outputPrice, retirementAt string) (*Handler, error) {
	profile, err := profileFor(profileName)
	if err != nil {
		return nil, err
	}
	if !apiKeyPattern.MatchString(key) {
		return nil, errors.New("invalid pilot key")
	}
	retirement, err := parseRetirement(retirementAt, time.Now())
	if err != nil {
		return nil, err
	}
	catalog, err := profile.catalog(provider, inputPrice, outputPrice, retirement)
	if err != nil {
		return nil, err
	}
	upstream, _ := url.Parse("http://127.0.0.1:8000")
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableKeepAlives = true
	transport.ResponseHeaderTimeout = 120 * time.Second
	h := &Handler{profile: profile, key: sha256.Sum256([]byte("Bearer " + key)), catalog: catalog, slots: make(chan struct{}, profile.concurrent),
		retirement: retirement, now: time.Now}
	h.proxy = &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(upstream)
			// Do not forward caller-selected credentials, forwarding headers or queries.
			pr.Out.Header = http.Header{"Content-Type": {"application/json"}, "Authorization": {"Bearer " + key}}
			pr.Out.URL.RawQuery = ""
		}, Transport: transport, FlushInterval: -1, ErrorLog: log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			writeError(w, 502, "upstream_unavailable")
		},
	}
	return h, nil
}

func parseRetirement(value string, now time.Time) (time.Time, error) {
	if value == "" { // Loopback-only local proofs do not need an operator cutoff.
		return time.Time{}, nil
	}
	cutoff, err := time.Parse(time.RFC3339, value)
	if err != nil || cutoff.UTC().Format(time.RFC3339) != value || !cutoff.After(now) || cutoff.Sub(now) > 24*time.Hour {
		return time.Time{}, errors.New("retirement must be a future UTC second within 24 hours")
	}
	// Preserve a monotonic deadline so a wall-clock rollback cannot extend a run.
	return now.Add(cutoff.Sub(now)), nil
}

func (h *Handler) expired() bool {
	now := h.now()
	return !h.retirement.IsZero() && (!now.Before(h.retirement) || !now.UTC().Before(h.retirement.UTC()))
}

func writeError(w http.ResponseWriter, status int, code string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": code, "type": code}})
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.URL.RawQuery != "" || r.URL.RawPath != "" {
		writeError(w, 400, "unsupported_url")
		return
	}
	if h.expired() {
		writeError(w, 503, "pilot_retired")
		return
	}
	if r.Method == http.MethodGet && r.URL.Path == "/health" {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		r = r.Clone(ctx)
		h.proxy.ServeHTTP(w, r)
		return
	}
	// The published catalog contains no credentials or private deployment data.
	if r.Method == http.MethodGet && r.URL.Path == "/v1/models" {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(h.catalog)
		return
	}
	provided := sha256.Sum256([]byte(r.Header.Get("Authorization")))
	if len(r.Header.Values("Authorization")) != 1 || subtle.ConstantTimeCompare(provided[:], h.key[:]) != 1 {
		writeError(w, 401, "invalid_api_key")
		return
	}
	if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
		writeError(w, 404, "unsupported_endpoint")
		return
	}
	select {
	case h.slots <- struct{}{}:
		defer func() { <-h.slots }()
	default:
		w.Header().Set("Retry-After", "1")
		writeError(w, 429, "capacity_exhausted")
		return
	}
	controller := http.NewResponseController(w)
	_ = controller.SetReadDeadline(time.Now().Add(30 * time.Second))
	defer r.Body.Close()
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, int64(h.profile.bodyLimit())))
	_ = controller.SetReadDeadline(time.Time{})
	if err != nil {
		writeError(w, 413, "invalid_request_body")
		return
	}
	body, err = h.profile.boundRequest(body)
	if err != nil {
		writeError(w, 400, "unsupported_pilot_request")
		return
	}
	// A slow upload must not enter inference after the advertised cutoff.
	if h.expired() {
		writeError(w, 503, "pilot_retired")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	_ = controller.SetWriteDeadline(time.Now().Add(120 * time.Second))
	defer func() { _ = controller.SetWriteDeadline(time.Time{}) }()
	r = r.Clone(ctx)
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	r.GetBody = nil
	h.proxy.ServeHTTP(w, r)
}

// BoundRequest serializes the validated object again so duplicate JSON keys
// cannot mean different things to this guard and the downstream parser.
func BoundRequest(body []byte) ([]byte, error) {
	return qwenProfile().boundRequest(body)
}

func (p serviceProfile) boundRequest(body []byte) ([]byte, error) {
	if p.nativeTools && !boundedToolJSON(body, p.bodyLimit()) {
		return nil, errSelectedTools
	}
	var value map[string]json.RawMessage
	if json.Unmarshal(body, &value) != nil || value == nil {
		return nil, errors.New("invalid JSON")
	}
	var model string
	if json.Unmarshal(value["model"], &model) != nil || model != p.model {
		return nil, errors.New("unsupported model")
	}
	if p.nativeTools {
		if err := validateSelectedTools(value, p.bodyLimit()); err != nil {
			return nil, err
		}
	} else {
		var messages []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		}
		if json.Unmarshal(value["messages"], &messages) != nil || len(messages) == 0 || len(messages) > 128 {
			return nil, errors.New("text messages required")
		}
		for _, message := range messages {
			if message.Role != "system" && message.Role != "user" && message.Role != "assistant" {
				return nil, errors.New("unsupported role")
			}
		}
	}
	if p.nativeReasoning {
		if err := normalizeSelectedReasoning(value); err != nil {
			return nil, err
		}
	}
	// Only the trusted upstream holding the provider key may supply a cache
	// namespace. Omission isolates this request instead of sharing a global cache.
	var salt json.RawMessage
	if p.longContext {
		var err error
		salt, err = selectedCacheSalt(value["cache_salt"])
		if err != nil {
			return nil, err
		}
		delete(value, "cache_salt")
	}
	if err := validateOptions(value, p.reasoningHints, p.nativeReasoning, p.nativeTools); err != nil {
		return nil, err
	}
	if raw, ok := value["n"]; ok {
		var n int
		if json.Unmarshal(raw, &n) != nil || n != 1 {
			return nil, errors.New("one completion required")
		}
	}
	limit := p.maxOutput
	specified := false
	for _, field := range []string{"max_tokens", "max_completion_tokens"} {
		if raw, ok := value[field]; ok {
			specified = true
			var n int
			if bytes.Equal(raw, []byte("null")) || json.Unmarshal(raw, &n) != nil || n < 1 || n > p.maxOutput {
				return nil, errors.New("invalid output limit")
			}
			if n < limit {
				limit = n
			}
		}
	}
	if !specified {
		limit = p.defaultOutput()
	}
	value["max_tokens"], _ = json.Marshal(limit)
	delete(value, "max_completion_tokens")
	if p.longContext {
		value["cache_salt"] = salt
	}
	return json.Marshal(value)
}

// The engine also accepts native sampling, grammar and template controls. Only
// this small, bounded OpenAI subset may cross the public pilot boundary.
func validateOptions(value map[string]json.RawMessage, reasoningHints, nativeReasoning, nativeTools bool) error {
	invalid := errors.New("unsupported pilot option")
	for field, raw := range value {
		if bytes.Equal(raw, []byte("null")) {
			return invalid
		}
		switch field {
		case "model", "max_tokens", "max_completion_tokens", "n":
			// Validated by BoundRequest.
		case "messages":
			if nativeTools {
				continue // Exact fields, text and tool history were validated together.
			}
			var messages []map[string]json.RawMessage
			if json.Unmarshal(raw, &messages) != nil {
				return invalid
			}
			for _, message := range messages {
				if len(message) != 2 || message["role"] == nil || message["content"] == nil || bytes.Equal(message["content"], []byte("null")) {
					return invalid
				}
			}
		case "tools", "tool_choice", "parallel_tool_calls":
			if !nativeTools {
				return invalid
			}
		case "stream":
			var enabled bool
			if json.Unmarshal(raw, &enabled) != nil {
				return invalid
			}
		case "stream_options":
			var options map[string]json.RawMessage
			var enabled bool
			if json.Unmarshal(raw, &options) != nil || len(options) != 1 || bytes.Equal(options["include_usage"], []byte("null")) || json.Unmarshal(options["include_usage"], &enabled) != nil {
				return invalid
			}
		case "temperature", "top_p", "frequency_penalty", "presence_penalty":
			var number float64
			low, high := 0.0, 2.0
			if field == "top_p" {
				high = 1
			} else if field == "frequency_penalty" || field == "presence_penalty" {
				low = -2
			}
			if json.Unmarshal(raw, &number) != nil || number < low || number > high {
				return invalid
			}
		case "top_k", "seed":
			var number int64
			low, high := int64(0), int64(2147483647)
			if field == "top_k" {
				low, high = 1, 100
			}
			if json.Unmarshal(raw, &number) != nil || number < low || number > high {
				return invalid
			}
		case "stop":
			var text string
			var stops []string
			if json.Unmarshal(raw, &text) == nil {
				stops = []string{text}
			} else if json.Unmarshal(raw, &stops) != nil {
				return invalid
			}
			if len(stops) == 0 || len(stops) > 4 {
				return invalid
			}
			for _, stop := range stops {
				if len(stop) == 0 || len(stop) > 1024 {
					return invalid
				}
			}
		case "thinking", "reasoning", "reasoning_effort":
			if nativeReasoning {
				var effort string
				if field != "reasoning_effort" || json.Unmarshal(raw, &effort) != nil || !selectedEffort(effort) {
					return invalid
				}
				continue
			}
			if !reasoningHints {
				return invalid
			}
			// Compatibility probes may send these non-thinking hints. Never pass
			// native reasoning budgets or template overrides into the engine.
			var hint string
			if field == "reasoning_effort" {
				if json.Unmarshal(raw, &hint) != nil || (hint != "low" && hint != "none") {
					return invalid
				}
			} else {
				var options map[string]string
				if json.Unmarshal(raw, &options) != nil || len(options) != 1 {
					return invalid
				}
				if field == "thinking" && options["type"] != "disabled" {
					return invalid
				}
				if field == "reasoning" && options["effort"] != "low" && options["effort"] != "none" {
					return invalid
				}
			}
			delete(value, field)
		default:
			return invalid
		}
	}
	return nil
}

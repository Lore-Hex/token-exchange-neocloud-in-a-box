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
	return qwenProfile().catalog(provider, Pricing{Input: inputPrice, Output: outputPrice}, retirement)
}

func (p serviceProfile) catalog(provider ProviderConfig, pricing Pricing, retirement time.Time) ([]byte, error) {
	if err := provider.Validate(); err != nil {
		return nil, err
	}
	cachedInput, err := pricing.cachedInput(p)
	if err != nil {
		return nil, err
	}
	var retirementAt any
	if !retirement.IsZero() {
		retirementAt = retirement.UTC().Format(time.RFC3339)
	}
	owner, _, _ := strings.Cut(p.model, "/")
	version := "2.0"
	capabilities := map[string]bool{"streaming": true, "tools": false, "structured_output": false, "reasoning": false, "prompt_caching": false}
	if p.context == 1048576 {
		// Explicit v2.1 preserves the v2.0 900-second ceiling. These capabilities
		// were qualified on the million-token profile; older declarations stay fixed.
		version = "2.1"
		capabilities["tools"], capabilities["prompt_caching"] = true, true
	}
	document := map[string]any{
		"object": "list", "contract_version": version,
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
			"capabilities": capabilities,
			"pricing": map[string]any{"currency": "USD", "unit": "per_1m_tokens", "input": pricing.Input,
				"output": pricing.Output, "cached_input": cachedInput, "cache_write": nil, "minimum_request": "0"},
			"lifecycle": map[string]any{"status": "active", "deprecation_at": nil, "retirement_at": retirementAt, "replacement_model_id": nil},
			"reliability": map[string]any{"first_token_timeout_seconds": 30, "completion_timeout_seconds": int(p.requestTimeout() / time.Second),
				"stream_idle_timeout_seconds": 30, "capacity_scope": "model"},
		}},
	}
	return json.Marshal(document)
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

// ShutdownGrace keeps active inference alive after admissions stop. The outer
// TLS shim and owner must provide at least this much drain time as well.
func (h *Handler) ShutdownGrace() time.Duration {
	return h.profile.requestTimeout() + 10*time.Second
}

// New fixes the backend to loopback. Neither requests nor environment variables
// can supply an alternate destination for provider credentials or prompt bytes.
func New(provider ProviderConfig, key, inputPrice, outputPrice, retirementAt string) (*Handler, error) {
	return NewWithProfile(ProfileQwen, provider, key, inputPrice, outputPrice, retirementAt)
}

// NewWithProfile selects one closed profile at startup. It keeps New's fixed
// loopback backend and requires the engine to serve the catalog ID unchanged.
func NewWithProfile(profileName string, provider ProviderConfig, key, inputPrice, outputPrice, retirementAt string) (*Handler, error) {
	return NewWithLifetime(profileName, provider, key, inputPrice, outputPrice, retirementAt, "ordinary-24h")
}

// NewWithLifetime opts the qualified million-token service into one bounded
// weekly lease. It does not renew leases or change any request/cleanup limits.
func NewWithLifetime(profileName string, provider ProviderConfig, key, inputPrice, outputPrice, retirementAt, lifetime string) (*Handler, error) {
	return NewWithPricing(profileName, provider, key, Pricing{Input: inputPrice, Output: outputPrice}, retirementAt, lifetime)
}

// NewWithPricing freezes the operator's explicit tariff into the startup
// catalog. It does not change usage, cache isolation, admission or billing.
func NewWithPricing(profileName string, provider ProviderConfig, key string, pricing Pricing, retirementAt, lifetime string) (*Handler, error) {
	limit := 24 * time.Hour
	switch lifetime {
	case "ordinary-24h":
	case "ordinary-7d":
		if profileName != ProfileSelectedMillion || retirementAt == "" {
			return nil, errors.New("weekly lease requires the million profile and an explicit cutoff")
		}
		limit *= 7
	default:
		return nil, errors.New("unsupported serving lifetime")
	}
	profile, err := profileFor(profileName)
	if err != nil {
		return nil, err
	}
	if !apiKeyPattern.MatchString(key) {
		return nil, errors.New("invalid pilot key")
	}
	retirement, err := parseRetirementWindow(retirementAt, time.Now(), limit)
	if err != nil {
		return nil, err
	}
	catalog, err := profile.catalog(provider, pricing, retirement)
	if err != nil {
		return nil, err
	}
	upstream, _ := url.Parse("http://127.0.0.1:8000")
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableKeepAlives = true
	transport.ResponseHeaderTimeout = profile.requestTimeout()
	h := &Handler{profile: profile, key: sha256.Sum256([]byte("Bearer " + key)), catalog: catalog, slots: make(chan struct{}, profile.concurrent),
		retirement: retirement, now: time.Now}
	h.proxy = &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(upstream)
			// Do not forward caller-selected credentials, forwarding headers or queries.
			pr.Out.Header = http.Header{"Content-Type": {"application/json"}, "Authorization": {"Bearer " + key}}
			if rank, ok := pr.In.Context().Value(cacheRankContextKey{}).(string); profile.longContext && ok && (rank == "0" || rank == "1") {
				pr.Out.Header.Set("X-data-parallel-rank", rank)
			}
			pr.Out.URL.RawQuery = ""
		}, Transport: transport, FlushInterval: -1, ErrorLog: log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			writeError(w, 502, "upstream_unavailable")
		},
	}
	return h, nil
}

func parseRetirement(value string, now time.Time) (time.Time, error) {
	return parseRetirementWindow(value, now, 24*time.Hour)
}

func parseRetirementWindow(value string, now time.Time, limit time.Duration) (time.Time, error) {
	if value == "" { // Loopback-only local proofs do not need an operator cutoff.
		return time.Time{}, nil
	}
	cutoff, err := time.Parse(time.RFC3339, value)
	if err != nil || cutoff.UTC().Format(time.RFC3339) != value || !cutoff.After(now) || cutoff.Sub(now) > limit {
		return time.Time{}, errors.New("retirement must be a future UTC second within the selected lease")
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
	bounded, err := h.profile.normalizeRequest(body)
	if err != nil {
		writeError(w, 400, "unsupported_pilot_request")
		return
	}
	// A slow upload must not enter inference after the advertised cutoff.
	if h.expired() {
		writeError(w, 503, "pilot_retired")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), h.profile.requestTimeout())
	defer cancel()
	if h.profile.longContext {
		ctx = context.WithValue(ctx, cacheRankContextKey{}, bounded.cacheRank)
	}
	_ = controller.SetWriteDeadline(time.Now().Add(h.profile.requestTimeout()))
	defer func() { _ = controller.SetWriteDeadline(time.Time{}) }()
	r = r.Clone(ctx)
	r.Body = io.NopCloser(bytes.NewReader(bounded.body))
	r.ContentLength = int64(len(bounded.body))
	r.GetBody = nil
	h.proxy.ServeHTTP(w, r)
}

// BoundRequest serializes the validated object again so duplicate JSON keys
// cannot mean different things to this guard and the downstream parser.
func BoundRequest(body []byte) ([]byte, error) {
	return qwenProfile().boundRequest(body)
}

func (p serviceProfile) boundRequest(body []byte) ([]byte, error) {
	request, err := p.normalizeRequest(body)
	return request.body, err
}

type normalizedRequest struct {
	body      []byte
	cacheRank string
}

func (p serviceProfile) normalizeRequest(body []byte) (normalizedRequest, error) {
	if p.nativeTools && !boundedToolJSON(body, p.bodyLimit()) {
		return normalizedRequest{}, errSelectedTools
	}
	var value map[string]json.RawMessage
	if json.Unmarshal(body, &value) != nil || value == nil {
		return normalizedRequest{}, errors.New("invalid JSON")
	}
	var model string
	if json.Unmarshal(value["model"], &model) != nil || model != p.model {
		return normalizedRequest{}, errors.New("unsupported model")
	}
	if p.nativeTools {
		if err := validateSelectedTools(value, p.bodyLimit()); err != nil {
			return normalizedRequest{}, err
		}
	} else {
		var messages []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		}
		if json.Unmarshal(value["messages"], &messages) != nil || len(messages) == 0 || len(messages) > 128 {
			return normalizedRequest{}, errors.New("text messages required")
		}
		for _, message := range messages {
			if message.Role != "system" && message.Role != "user" && message.Role != "assistant" {
				return normalizedRequest{}, errors.New("unsupported role")
			}
		}
	}
	if p.nativeReasoning {
		if err := normalizeSelectedReasoning(value); err != nil {
			return normalizedRequest{}, err
		}
	}
	// Only the trusted upstream holding the provider key may supply a cache
	// namespace. Omission isolates this request instead of sharing a global cache.
	var salt json.RawMessage
	if p.longContext {
		var err error
		salt, err = selectedCacheSalt(value["cache_salt"])
		if err != nil {
			return normalizedRequest{}, err
		}
		delete(value, "cache_salt")
	}
	if err := validateOptions(value, p.reasoningHints, p.nativeReasoning, p.nativeTools); err != nil {
		return normalizedRequest{}, err
	}
	if raw, ok := value["n"]; ok {
		var n int
		if json.Unmarshal(raw, &n) != nil || n != 1 {
			return normalizedRequest{}, errors.New("one completion required")
		}
	}
	limit := p.maxOutput
	specified := false
	for _, field := range []string{"max_tokens", "max_completion_tokens"} {
		if raw, ok := value[field]; ok {
			specified = true
			var n int
			if bytes.Equal(raw, []byte("null")) || json.Unmarshal(raw, &n) != nil || n < 1 || n > p.maxOutput {
				return normalizedRequest{}, errors.New("invalid output limit")
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
	var rank string
	if p.longContext {
		value["cache_salt"] = salt
		rank = selectedCacheRank(salt)
	}
	encoded, err := json.Marshal(value)
	return normalizedRequest{body: encoded, cacheRank: rank}, err
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

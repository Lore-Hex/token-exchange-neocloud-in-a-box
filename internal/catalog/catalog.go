// SPDX-License-Identifier: Apache-2.0

// Package catalog builds offline, synthetic Catalog v2 declarations. Schema
// validity is not deployment readiness, attestation, or permission to publish.
package catalog

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"sort"
	"strings"
	"sync"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"github.com/santhosh-tekuri/jsonschema/v6"
)

const SchemaURL = "https://trustedrouter.com/providers/marketplace/catalog.v2.schema.json"
const SchemaSHA256 = "d4d06605dd62dd471955af41b1ac88dd51bf1927e1127d3b3526693fcd7b0f47"
const pricingSchemaURL = "https://schemas.neocloud.invalid/nib/catalog-pricing.schema.json"

//go:embed catalog.v2.schema.json pricing.schema.json
var schemaFiles embed.FS

var schemas = sync.OnceValues(func() (map[string]*jsonschema.Schema, error) {
	c := jsonschema.NewCompiler()
	c.AssertFormat()
	for name, url := range map[string]string{"catalog.v2.schema.json": SchemaURL, "pricing.schema.json": pricingSchemaURL} {
		data, err := schemaFiles.ReadFile(name)
		if err != nil {
			return nil, err
		}
		if url == SchemaURL {
			digest := sha256.Sum256(data)
			if hex.EncodeToString(digest[:]) != SchemaSHA256 {
				return nil, errors.New("pinned public catalog schema hash mismatch")
			}
		}
		value, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		if err := c.AddResource(url, value); err != nil {
			return nil, err
		}
	}
	// No URL loader: references resolve only to the embedded resources.
	out := make(map[string]*jsonschema.Schema)
	for _, url := range []string{SchemaURL, pricingSchemaURL} {
		s, err := c.Compile(url)
		if err != nil {
			return nil, err
		}
		out[url] = s
	}
	return out, nil
})

type declaration struct {
	Models []struct {
		Pack            string         `json:"pack"`
		Profile         string         `json:"profile"`
		MaxOutputTokens int64          `json:"max_output_tokens"`
		Pricing         map[string]any `json:"pricing"`
	} `json:"models"`
}

type modelPack struct {
	ID              string `json:"id"`
	TRModelID       string `json:"tr_model_id"`
	ServedModelName string `json:"served_model_name"`
	Files           string `json:"files"`
	TotalBytes      int64  `json:"total_bytes"`
	EngineProfiles  map[string]struct {
		MaxModelLen int64 `json:"max_model_len"`
	} `json:"engine_profiles"`
}

// Generate reads confined local paths and returns complete, validated JSON with
// sorted models and object keys and a trailing newline. It never uses clocks,
// credentials, network access, floating-point prices, or deployment claims.
func Generate(root, pricingPath string) ([]byte, error) {
	r, err := os.OpenRoot(root)
	if err != nil {
		return nil, errors.New("cannot open repository root")
	}
	defer r.Close()
	data, err := readRegular(r, pricingPath, config.MaxDocumentBytes)
	if err != nil {
		return nil, fmt.Errorf("pricing: %w", err)
	}
	value, err := decode(data)
	if err != nil {
		return nil, err
	}
	if err := validate(pricingSchemaURL, value); err != nil {
		return nil, errors.New("pricing schema failed: only synthetic-local-test declarations and exact known fields are accepted")
	}
	var input declaration
	if err := convert(value, &input); err != nil {
		return nil, err
	}
	models := make([]map[string]any, 0, len(input.Models))
	seenIDs, seenNative := map[string]bool{}, map[string]bool{}
	for i, model := range input.Models {
		pack, err := loadPack(r, model.Pack)
		if err != nil {
			return nil, fmt.Errorf("model %d: %w", i+1, err)
		}
		profile, ok := pack.EngineProfiles[model.Profile]
		if !ok || model.MaxOutputTokens > profile.MaxModelLen {
			return nil, fmt.Errorf("model %d: missing profile or output limit exceeds context", i+1)
		}
		if seenIDs[pack.TRModelID] || seenNative[pack.ServedModelName] {
			return nil, errors.New("duplicate canonical or native model ID")
		}
		seenIDs[pack.TRModelID], seenNative[pack.ServedModelName] = true, true
		for _, price := range model.Pricing {
			if s, ok := price.(string); ok && len(s) > 128 {
				return nil, errors.New("price representation exceeds 128 bytes")
			}
		}
		models = append(models, map[string]any{
			"id": pack.TRModelID, "object": "model", "owned_by": strings.SplitN(pack.TRModelID, "/", 2)[0],
			"name": "SYNTHETIC LOCAL TEST ONLY - " + pack.ID, "type": "chat",
			"context_length": profile.MaxModelLen, "max_output_tokens": model.MaxOutputTokens,
			"endpoints": []string{"chat/completions"}, "input_modalities": []string{"text"}, "output_modalities": []string{"text"},
			"capabilities": map[string]bool{"streaming": false, "tools": false, "structured_output": false, "reasoning": false, "prompt_caching": false},
			"pricing":      model.Pricing,
			"lifecycle":    map[string]any{"status": "active", "deprecation_at": nil, "retirement_at": nil, "replacement_model_id": nil},
			"reliability":  map[string]any{"first_token_timeout_seconds": 30, "completion_timeout_seconds": 60, "stream_idle_timeout_seconds": 30, "capacity_scope": "model"},
		})
	}
	sort.Slice(models, func(i, j int) bool { return models[i]["id"].(string) < models[j]["id"].(string) })
	output := map[string]any{
		"object": "list", "contract_version": "2.0", "data": models,
		"provider": map[string]any{
			"id": "nib-synthetic-local-test", "status_url": "https://nib.invalid/synthetic-only",
			"support_contact": "mailto:synthetic-only@nib.invalid", "incident_contact": "mailto:synthetic-only@nib.invalid",
			"regions": []string{"local-test-only"}, "request_id_header": "X-Request-Id",
			"error_contract": map[string]any{"rate_limit_status": 429, "overload_status": 503, "retry_after_header": "Retry-After", "account_quota_error_codes": []string{}},
		},
	}
	data, err = json.MarshalIndent(output, "", "  ")
	if err != nil {
		return nil, errors.New("cannot encode catalog")
	}
	if err := Validate(data); err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

// Validate checks the pinned public schema, not privacy, readiness, or approval.
func Validate(data []byte) error {
	if !json.Valid(data) {
		return errors.New("catalog must be one JSON document")
	}
	value, err := decode(data)
	if err != nil {
		return err
	}
	return validate(SchemaURL, value)
}

func validate(url string, value any) error {
	all, err := schemas()
	if err != nil {
		return errors.New("cannot compile pinned catalog schemas")
	}
	if err := all[url].Validate(value); err != nil {
		return errors.New("catalog schema validation failed")
	}
	return nil
}

func convert(value, target any) error {
	data, err := json.Marshal(value)
	if err != nil || json.Unmarshal(data, target) != nil {
		return errors.New("cannot decode validated configuration")
	}
	return nil
}

func loadPack(root *os.Root, name string) (modelPack, error) {
	var pack modelPack
	data, err := readRegular(root, name, config.MaxDocumentBytes)
	if err != nil {
		return pack, err
	}
	if err := config.ValidateModelPack(data); err != nil {
		return pack, errors.New("model pack schema validation failed")
	}
	value, err := decode(data)
	if err != nil {
		return pack, err
	}
	if err := convert(value, &pack); err != nil {
		return pack, err
	}
	manifest, err := readRegular(root, path.Join(path.Dir(name), pack.Files), config.MaxManifestBytes)
	if err != nil {
		return pack, err
	}
	if err := config.ValidateFileManifest(manifest, pack.TotalBytes); err != nil {
		return pack, errors.New("model pack file manifest validation failed")
	}
	return pack, nil
}

func readRegular(root *os.Root, name string, limit int64) ([]byte, error) {
	if !fs.ValidPath(name) || strings.Contains(name, `\`) {
		return nil, errors.New("expected a repository-relative path without traversal")
	}
	info, err := root.Lstat(name)
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("expected an existing regular file, not a symlink or device")
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, errors.New("cannot open confined file")
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("expected a regular file")
	}
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errors.New("cannot read file or size exceeds limit")
	}
	return data, nil
}

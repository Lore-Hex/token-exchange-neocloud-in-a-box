// SPDX-License-Identifier: Apache-2.0

// Package config validates operator configuration. It must not be imported by
// guest code: its standards-based parsers are outside the guest dependency set.
package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/schemas"
	"github.com/santhosh-tekuri/jsonschema/v6"
	"go.yaml.in/yaml/v3"
)

const schemaBase = "https://schemas.neocloud.invalid/nib/"

// MaxDocumentBytes bounds configuration parsing, independently of weight sizes.
const MaxDocumentBytes = 4 << 20

// Kind selects an authoritative schema, not an input-provided schema URL.
type Kind string

const (
	ModelPack           Kind = "model-pack"
	DeployableModelPack Kind = "model-pack-deployable"
	HardwareProfile     Kind = "hardware-profile"
	Release             Kind = "release"
	Evidence            Kind = "evidence"
)

var compileSchemas = sync.OnceValues(func() (map[string]*jsonschema.Schema, error) {
	c := jsonschema.NewCompiler()
	c.DefaultDraft(jsonschema.Draft2020)
	c.AssertFormat()
	// The compiler has no URL loader. Only embedded resources and its bundled
	// metaschemas can resolve, even when an input supplies a schema-like URL.
	names, err := schemas.Files.ReadDir(".")
	if err != nil {
		return nil, err
	}
	for _, name := range names {
		data, err := schemas.Files.ReadFile(name.Name())
		if err != nil {
			return nil, err
		}
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		if err := c.AddResource(schemaBase+name.Name(), doc); err != nil {
			return nil, err
		}
	}
	out := make(map[string]*jsonschema.Schema)
	for _, name := range []string{string(ModelPack), string(DeployableModelPack), string(HardwareProfile), string(Release), string(Evidence), "common", "model-pack-confidential"} {
		location := schemaBase + name + ".schema.json"
		if name == "common" {
			location += "#/$defs/fileEntry"
		}
		s, err := c.Compile(location)
		if err != nil {
			return nil, fmt.Errorf("compile embedded %s schema: %w", name, err)
		}
		out[name] = s
	}
	return out, nil
})

// Validate validates one document. Packs/profiles accept JSON-compatible YAML;
// releases/evidence require JSON. It never performs network or hardware checks.
func Validate(kind Kind, data []byte) error {
	_, err := validateDocument(kind, data)
	return err
}

// ValidateModelPack accepts draft packs, including an empty verity hash.
func ValidateModelPack(data []byte) error { return Validate(ModelPack, data) }

// ValidateDeployableModelPack additionally requires complete weight/engine pins.
// Repository validation is still needed to check external file references.
func ValidateDeployableModelPack(data []byte) error { return Validate(DeployableModelPack, data) }

// ValidateHardwareProfile checks a hardware configuration, not actual hardware.
func ValidateHardwareProfile(data []byte) error { return Validate(HardwareProfile, data) }

// ValidateRelease checks the manifest structure, not signatures or attestation.
func ValidateRelease(data []byte) error { return Validate(Release, data) }

// ValidateEvidence checks run-record consistency, not the truth of its claims.
func ValidateEvidence(data []byte) error { return Validate(Evidence, data) }

func validateDocument(kind Kind, data []byte) (map[string]any, error) {
	all, err := compileSchemas()
	if err != nil {
		return nil, err
	}
	s, ok := all[string(kind)]
	if !ok || kind == "common" {
		return nil, errors.New("unknown configuration kind")
	}
	v, err := decode(data, kind == Release || kind == Evidence)
	if err != nil {
		return nil, err
	}
	if err := validateValue(s, v); err != nil {
		return nil, err
	}
	doc := v.(map[string]any)
	switch kind {
	case Evidence:
		if doc["execution"] != "not_run" {
			start, err := time.Parse(time.RFC3339Nano, doc["started_utc"].(string))
			if err != nil {
				return nil, errors.New("/started_utc: unsupported UTC timestamp")
			}
			end, err := time.Parse(time.RFC3339Nano, doc["ended_utc"].(string))
			if err != nil {
				return nil, errors.New("/ended_utc: unsupported UTC timestamp")
			}
			if end.Before(start) {
				return nil, errors.New("/ended_utc: precedes started_utc")
			}
		}
	case HardwareProfile:
		seen := make(map[string]bool)
		gpus := 0
		for _, item := range doc["pci_devices"].([]any) {
			device := item.(map[string]any)
			address := device["address"].(string)
			if seen[address] {
				return nil, errors.New("/pci_devices: duplicate PCI address")
			}
			seen[address] = true
			if device["kind"] == "gpu" {
				gpus++
			}
		}
		count, err := integer(doc["gpu"].(map[string]any)["count"])
		if err != nil {
			return nil, err
		}
		if int64(gpus) != count {
			return nil, errors.New("/pci_devices: GPU device count differs from gpu.count")
		}
	}
	return doc, nil
}

func integer(value any) (int64, error) {
	n, ok := value.(json.Number)
	if !ok {
		return 0, errors.New("expected a JSON number")
	}
	r, ok := new(big.Rat).SetString(string(n))
	if !ok || !r.IsInt() || !r.Num().IsInt64() {
		return 0, errors.New("expected an exact int64")
	}
	return r.Num().Int64(), nil
}

// Keep diagnostics useful without echoing commands, environment values or
// other potentially sensitive configuration contents from library errors.
func validateValue(s *jsonschema.Schema, v any) error {
	err := s.Validate(v)
	if err == nil {
		return nil
	}
	var validation *jsonschema.ValidationError
	if !errors.As(err, &validation) {
		return errors.New("schema validation failed")
	}
	var issues []string
	var visit func(*jsonschema.ValidationError)
	visit = func(e *jsonschema.ValidationError) {
		if len(e.Causes) != 0 {
			for _, cause := range e.Causes {
				visit(cause)
			}
			return
		}
		issues = append(issues, fmt.Sprintf("/%s: schema %s failed", strings.Join(e.InstanceLocation, "/"), strings.Join(e.ErrorKind.KeywordPath(), "/")))
	}
	visit(validation)
	sort.Strings(issues)
	return errors.New(strings.Join(issues, "; "))
}

func decode(data []byte, requireJSON bool) (any, error) {
	if len(data) > MaxDocumentBytes {
		return nil, errors.New("configuration exceeds size limit")
	}
	if !utf8.Valid(data) {
		return nil, errors.New("configuration must be UTF-8")
	}
	if requireJSON && !json.Valid(data) {
		return nil, errors.New("configuration must be one JSON document")
	}
	dec := yaml.NewDecoder(bytes.NewReader(data))
	var node yaml.Node
	if err := dec.Decode(&node); err != nil {
		return nil, errors.New("invalid or empty YAML/JSON document")
	}
	var extra yaml.Node
	if err := dec.Decode(&extra); err != io.EOF {
		return nil, errors.New("configuration must contain exactly one document")
	}
	if err := checkYAML(&node, 0); err != nil {
		return nil, err
	}
	if json.Valid(data) {
		return jsonschema.UnmarshalJSON(bytes.NewReader(data))
	}
	return yamlValue(&node)
}

func yamlValue(n *yaml.Node) (any, error) {
	switch n.Kind {
	case yaml.DocumentNode:
		return yamlValue(n.Content[0])
	case yaml.MappingNode:
		v := make(map[string]any, len(n.Content)/2)
		for i := 0; i < len(n.Content); i += 2 {
			item, err := yamlValue(n.Content[i+1])
			if err != nil {
				return nil, err
			}
			v[n.Content[i].Value] = item
		}
		return v, nil
	case yaml.SequenceNode:
		v := make([]any, len(n.Content))
		for i, child := range n.Content {
			item, err := yamlValue(child)
			if err != nil {
				return nil, err
			}
			v[i] = item
		}
		return v, nil
	case yaml.ScalarNode:
		if n.Tag == "!!float" || n.Tag == "!!int" {
			// Do not round through float64: schema integer/maximum assertions
			// must see the original mathematical value, including exponents.
			num, err := jsonschema.UnmarshalJSON(strings.NewReader(n.Value))
			if _, ok := num.(json.Number); err != nil || !ok || len(n.Value) > 128 {
				return nil, errors.New("YAML number must use JSON numeric syntax")
			}
			return num, nil
		}
		var value any
		if err := n.Decode(&value); err != nil {
			return nil, errors.New("invalid YAML scalar")
		}
		data, err := json.Marshal(value)
		if err != nil {
			return nil, errors.New("YAML scalar must be JSON-compatible")
		}
		return jsonschema.UnmarshalJSON(bytes.NewReader(data))
	}
	return nil, errors.New("unsupported YAML value")
}

func checkYAML(node *yaml.Node, depth int) error {
	if depth > 64 {
		return errors.New("configuration nesting exceeds limit")
	}
	if node.Kind == yaml.AliasNode || node.Anchor != "" {
		return fmt.Errorf("line %d: YAML aliases and anchors are not supported", node.Line)
	}
	switch node.Tag {
	case "", "!!map", "!!seq", "!!str", "!!bool", "!!int", "!!float", "!!null":
	default:
		return fmt.Errorf("line %d: non-JSON YAML tag is not supported", node.Line)
	}
	if node.Kind == yaml.MappingNode {
		seen := make(map[string]bool)
		for i := 0; i < len(node.Content); i += 2 {
			key := node.Content[i]
			if key.Kind != yaml.ScalarNode || key.Tag != "!!str" {
				return fmt.Errorf("line %d: object keys must be strings", key.Line)
			}
			if seen[key.Value] {
				return fmt.Errorf("line %d: duplicate object key", key.Line)
			}
			seen[key.Value] = true
		}
	}
	for _, child := range node.Content {
		if err := checkYAML(child, depth+1); err != nil {
			return err
		}
	}
	return nil
}

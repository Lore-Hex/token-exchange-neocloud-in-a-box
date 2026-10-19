// SPDX-License-Identifier: Apache-2.0

package catalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"unicode/utf8"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
	"go.yaml.in/yaml/v3"
)

var integerSyntax = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)

// Parse nodes instead of decoding YAML into strings (which silently coerces
// numeric prices) or interface values (which can round through float64).
func decode(data []byte) (any, error) {
	if len(data) > config.MaxDocumentBytes || !utf8.Valid(data) {
		return nil, errors.New("document exceeds limit or is not UTF-8")
	}
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	var node, extra yaml.Node
	if err := decoder.Decode(&node); err != nil {
		return nil, errors.New("invalid YAML document")
	}
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("expected exactly one YAML document")
	}
	return nodeValue(&node, 0)
}

func nodeValue(n *yaml.Node, depth int) (any, error) {
	if depth > 32 || n.Anchor != "" || n.Kind == yaml.AliasNode {
		return nil, errors.New("anchors, aliases or excessive nesting are not supported")
	}
	switch n.Kind {
	case yaml.DocumentNode:
		if len(n.Content) == 1 {
			return nodeValue(n.Content[0], depth+1)
		}
	case yaml.MappingNode:
		if n.Tag != "!!map" {
			break
		}
		value := make(map[string]any)
		for i := 0; i < len(n.Content); i += 2 {
			key := n.Content[i]
			if key.Kind != yaml.ScalarNode || key.Tag != "!!str" || key.Anchor != "" {
				return nil, errors.New("object keys must be strings")
			}
			if _, exists := value[key.Value]; exists {
				return nil, errors.New("duplicate object key")
			}
			child, err := nodeValue(n.Content[i+1], depth+1)
			if err != nil {
				return nil, err
			}
			value[key.Value] = child
		}
		return value, nil
	case yaml.SequenceNode:
		if n.Tag != "!!seq" {
			break
		}
		value := make([]any, len(n.Content))
		for i, child := range n.Content {
			var err error
			value[i], err = nodeValue(child, depth+1)
			if err != nil {
				return nil, err
			}
		}
		return value, nil
	case yaml.ScalarNode:
		switch n.Tag {
		case "!!str":
			return n.Value, nil
		case "!!null":
			return nil, nil
		case "!!bool":
			if n.Value == "true" || n.Value == "false" {
				return n.Value == "true", nil
			}
		case "!!int":
			if len(n.Value) <= 16 && integerSyntax.MatchString(n.Value) {
				return json.Number(n.Value), nil
			}
		}
	}
	return nil, errors.New("unsupported YAML value; prices must be quoted decimal strings, integers must use bounded decimal notation")
}

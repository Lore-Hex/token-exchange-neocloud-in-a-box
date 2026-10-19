//go:build nibfake

// SPDX-License-Identifier: Apache-2.0
package service

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/nftables"
)

const (
	configLimit = 64 << 10
	fileLimit   = 1 << 20
	totalLimit  = 4 << 20
)

type fakeDevice struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
}

type fakeWeight struct {
	Path   string `json:"path"`
	Bytes  int64  `json:"bytes"`
	SHA256 string `json:"sha256"`
}

type fakeConfig struct {
	Schema        string       `json:"schema"`
	EngineAddress string       `json:"engine_address"`
	Devices       []fakeDevice `json:"devices"`
	InventoryFile string       `json:"inventory_file"`
	Weights       []fakeWeight `json:"weights"`
	NetworkFile   string       `json:"network_file"`
	Egress        []string     `json:"egress"`
}

type fakeSession struct {
	config fakeConfig
	root   *os.Root
}

var safeName = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`)

func openFakeConfig(path string) (*fakeSession, error) {
	root, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return nil, errors.New("configuration unavailable")
	}
	s := &fakeSession{root: root}
	b, err := readFakeFile(root, filepath.Base(path), configLimit)
	if err == nil {
		err = strictJSON(b, &s.config)
	}
	if err == nil {
		err = s.config.validate()
	}
	if err != nil {
		root.Close()
		return nil, errors.New("invalid simulation configuration")
	}
	return s, nil
}

func (c fakeConfig) validate() error {
	bad := errors.New("invalid simulation configuration")
	a, err := netip.ParseAddrPort(c.EngineAddress)
	if c.Schema != "nib.guest-fake/v1" || err != nil || a.Addr().String() != "127.0.0.1" || a.Port() < 1024 || a.Port() == 8443 {
		return bad
	}
	if !safeName.MatchString(c.InventoryFile) || !safeName.MatchString(c.NetworkFile) || c.InventoryFile == c.NetworkFile || len(c.Devices) == 0 || len(c.Devices) > 16 || len(c.Weights) == 0 || len(c.Weights) > 32 || c.Egress == nil {
		return bad
	}
	seen := make(map[string]bool)
	gpus := 0
	for _, d := range c.Devices {
		if !safeName.MatchString(d.ID) || seen[d.ID] || (d.Kind != "gpu" && d.Kind != "switch") {
			return bad
		}
		seen[d.ID] = true
		if d.Kind == "gpu" {
			gpus++
		}
	}
	if gpus == 0 || gpus > 8 {
		return bad
	}
	seen = map[string]bool{c.InventoryFile: true, c.NetworkFile: true}
	var total int64
	for _, w := range c.Weights {
		h, err := hex.DecodeString(w.SHA256)
		if !safeName.MatchString(w.Path) || seen[w.Path] || w.Bytes <= 0 || w.Bytes > fileLimit || err != nil || len(h) != sha256.Size || strings.ToLower(w.SHA256) != w.SHA256 {
			return bad
		}
		seen[w.Path] = true
		total += w.Bytes
	}
	if total > totalLimit {
		return bad
	}
	if _, err := nftables.Render(c.Egress); err != nil {
		return bad
	}
	return nil
}

func readFakeFile(root *os.Root, name string, limit int64) ([]byte, error) {
	i, err := root.Lstat(name)
	if err != nil || !i.Mode().IsRegular() || i.Size() > limit {
		return nil, errors.New("fixture unavailable")
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, errors.New("fixture unavailable")
	}
	defer f.Close()
	i, err = f.Stat()
	if err != nil || !i.Mode().IsRegular() {
		return nil, errors.New("fixture unavailable")
	}
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(b)) > limit {
		return nil, errors.New("fixture read failed")
	}
	return b, nil
}

// Reject duplicate object keys too: encoding/json alone accepts last-wins keys.
func strictJSON(b []byte, value any) error {
	d := json.NewDecoder(bytes.NewReader(b))
	if err := jsonValue(d, 0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing JSON")
	}
	d = json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	return d.Decode(value)
}

func jsonValue(d *json.Decoder, depth int) error {
	if depth > 16 {
		return errors.New("JSON depth limit")
	}
	t, err := d.Token()
	if err != nil {
		return errors.New("invalid JSON")
	}
	delim, container := t.(json.Delim)
	if !container {
		if t == nil {
			return errors.New("null JSON value")
		}
		return nil
	}
	seen := make(map[string]bool)
	for d.More() {
		if delim == '{' {
			key, err := d.Token()
			name, ok := key.(string)
			if err != nil || !ok || seen[name] || name != strings.ToLower(name) {
				return errors.New("duplicate or invalid JSON key")
			}
			seen[name] = true
		}
		if err := jsonValue(d, depth+1); err != nil {
			return err
		}
	}
	_, err = d.Token()
	return err
}

// SPDX-License-Identifier: Apache-2.0
package pilotapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/mail"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"unicode"
	"unicode/utf8"
)

const MaxProviderConfigBytes = 16 << 10

var providerIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]*$`)
var hostLabelPattern = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$`)
var errProviderConfig = errors.New("invalid provider configuration")

// ProviderConfig is operator-declared public metadata, not an attestation of
// identity, location, availability, or privacy. There are no serving defaults.
type ProviderConfig struct {
	ID              string   `json:"id"`
	StatusURL       string   `json:"status_url"`
	SupportContact  string   `json:"support_contact"`
	IncidentContact string   `json:"incident_contact"`
	Regions         []string `json:"regions"`
}

// LoadProviderConfig snapshots a bounded regular file without following a
// symlink or blocking on a FIFO. Errors never echo operator-supplied content.
func LoadProviderConfig(path string) (ProviderConfig, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return ProviderConfig{}, errProviderConfig
	}
	file := os.NewFile(uintptr(fd), path)
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > MaxProviderConfigBytes {
		return ProviderConfig{}, errProviderConfig
	}
	data, err := io.ReadAll(io.LimitReader(file, MaxProviderConfigBytes+1))
	if err != nil || len(data) == 0 || len(data) > MaxProviderConfigBytes || !utf8.Valid(data) {
		return ProviderConfig{}, errProviderConfig
	}
	return decodeProviderConfig(data)
}

func decodeProviderConfig(data []byte) (ProviderConfig, error) {
	var config ProviderConfig
	fields := map[string]any{
		"id": &config.ID, "status_url": &config.StatusURL,
		"support_contact": &config.SupportContact, "incident_contact": &config.IncidentContact,
		"regions": &config.Regions,
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return ProviderConfig{}, errProviderConfig
	}
	// Decode exact field names once: struct decoding alone accepts case aliases
	// and duplicate keys, which can disagree with an operator's JSON tooling.
	for decoder.More() {
		token, err = decoder.Token()
		name, ok := token.(string)
		if err != nil || !ok || fields[name] == nil || decoder.Decode(fields[name]) != nil {
			return ProviderConfig{}, errProviderConfig
		}
		delete(fields, name)
	}
	if token, err = decoder.Token(); err != nil || token != json.Delim('}') || len(fields) != 0 {
		return ProviderConfig{}, errProviderConfig
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF || config.Validate() != nil {
		return ProviderConfig{}, errProviderConfig
	}
	return config, nil
}

// Validate is shared by file loading, Catalog and New, including direct callers.
func (p ProviderConfig) Validate() error {
	if !providerIDPattern.MatchString(p.ID) || !validProviderURL(p.StatusURL, false) ||
		!validProviderURL(p.SupportContact, true) || !validProviderURL(p.IncidentContact, true) || len(p.Regions) == 0 {
		return errProviderConfig
	}
	seen := make(map[string]bool, len(p.Regions))
	for _, region := range p.Regions {
		// Catalog v2 has no country enum: regions are unique nonempty strings.
		if !utf8.ValidString(region) || strings.TrimSpace(region) != region || region == "" ||
			strings.ContainsFunc(region, unicode.IsControl) || seen[region] {
			return errProviderConfig
		}
		seen[region] = true
	}
	data, err := json.Marshal(p)
	if err != nil || len(data) > MaxProviderConfigBytes {
		return errProviderConfig
	}
	return nil
}

// ValidatePublic rejects placeholder domains, without claiming that a public
// declaration proves domain ownership or that any service/contact is reachable.
func (p ProviderConfig) ValidatePublic() error {
	if err := p.Validate(); err != nil {
		return err
	}
	status, _ := url.Parse(p.StatusURL)
	hosts := []string{status.Hostname()}
	for _, contact := range []string{p.SupportContact, p.IncidentContact} {
		u, _ := url.Parse(contact)
		address, _ := url.PathUnescape(u.Opaque)
		_, host, _ := strings.Cut(address, "@")
		hosts = append(hosts, host)
	}
	for _, host := range hosts {
		host = strings.ToLower(strings.TrimSuffix(host, "."))
		for _, reserved := range []string{"invalid", "example", "test", "localhost", "example.com", "example.net", "example.org"} {
			if host == reserved || strings.HasSuffix(host, "."+reserved) {
				return errors.New("public provider configuration contains a reserved domain")
			}
		}
	}
	return nil
}

func validProviderURL(value string, contact bool) bool {
	if value == "" || strings.ContainsFunc(value, func(r rune) bool { return r <= ' ' || r > '~' }) ||
		strings.ContainsAny(value, "#\"<>\\^`{}|") {
		return false
	}
	u, err := url.Parse(value)
	if err != nil || u.User != nil {
		return false
	}
	if contact {
		if u.Scheme != "mailto" || u.Opaque == "" || u.Host != "" || u.RawQuery != "" || u.ForceQuery {
			return false
		}
		address, err := url.PathUnescape(u.Opaque)
		if err != nil || strings.ContainsFunc(address, func(r rune) bool { return r <= ' ' || r > '~' }) {
			return false
		}
		parsed, err := mail.ParseAddress(address)
		if err != nil || parsed.Name != "" || parsed.Address != address {
			return false
		}
		_, host, ok := strings.Cut(address, "@")
		return ok && validProviderHost(host)
	}
	if u.Scheme != "https" || u.Opaque != "" || !validProviderHost(u.Hostname()) || strings.HasSuffix(u.Host, ":") {
		return false
	}
	if strings.HasPrefix(u.Host, "[") && net.ParseIP(u.Hostname()) == nil {
		return false
	}
	if _, err := url.QueryUnescape(u.RawQuery); err != nil {
		return false
	}
	if u.Port() != "" {
		port, err := strconv.Atoi(u.Port())
		if err != nil || port < 1 || port > 65535 {
			return false
		}
	}
	return true
}

func validProviderHost(host string) bool {
	if net.ParseIP(host) != nil {
		return true
	}
	if len(host) > 253 {
		return false
	}
	for _, label := range strings.Split(strings.TrimSuffix(host, "."), ".") {
		if !hostLabelPattern.MatchString(label) {
			return false
		}
	}
	return true
}

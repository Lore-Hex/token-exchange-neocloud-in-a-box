// SPDX-License-Identifier: Apache-2.0

package config

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"strings"
)

type document struct {
	path  string
	data  []byte
	value map[string]any
}

// ValidateRepository validates pack.yaml/pack.yml under models/, YAML profiles
// under profiles/, every release.json, and run.json under evidence/. It also
// validates referenced files.sha256 and release-to-pack/profile consistency.
// Test fixtures, dependency/build directories and VCS metadata are excluded.
// Missing artifact directories are valid in an early repository skeleton.
func ValidateRepository(root string) error {
	r, err := os.OpenRoot(root)
	if err != nil {
		return fmt.Errorf("open repository: %w", err)
	}
	defer r.Close()
	var failures []error
	packs := make(map[string]document)
	profiles := make(map[string]document)
	var releases []document
	err = fs.WalkDir(r.FS(), ".", func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() && name != "." {
			switch entry.Name() {
			case ".git", ".worktrees", "node_modules", "vendor", "build", ".venv", "testdata", "tests":
				return fs.SkipDir
			}
		}
		if entry.Type()&fs.ModeSymlink != 0 {
			failures = append(failures, fmt.Errorf("%s: repository symlinks are not allowed in scanned paths", name))
			return nil
		}
		kind := repositoryKind(name)
		if kind == "" || entry.IsDir() {
			return nil
		}
		data, err := readRegular(r, name, MaxDocumentBytes)
		if err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", name, err))
			return nil
		}
		value, err := validateDocument(kind, data)
		if err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", name, err))
			return nil
		}
		doc := document{name, data, value}
		switch kind {
		case ModelPack:
			id := value["id"].(string)
			if previous, exists := packs[id]; exists {
				failures = append(failures, fmt.Errorf("%s: duplicate model pack id also present in %s", name, previous.path))
			}
			packs[id] = doc
			parts := strings.Split(id, "@")
			if path.Dir(name) != path.Join("models", parts[0], parts[1]) {
				failures = append(failures, fmt.Errorf("%s: pack id differs from models/<model>/<revision> directory", name))
			}
			manifestPath := path.Join(path.Dir(name), value["files"].(string))
			manifest, err := readRegular(r, manifestPath, MaxManifestBytes)
			if err == nil {
				var total int64
				total, err = integer(value["total_bytes"])
				if err == nil {
					err = ValidateFileManifest(manifest, total)
				}
			}
			if err != nil {
				failures = append(failures, fmt.Errorf("%s: %w", manifestPath, err))
			}
		case HardwareProfile:
			id := value["id"].(string)
			if previous, exists := profiles[id]; exists {
				failures = append(failures, fmt.Errorf("%s: duplicate hardware profile id also present in %s", name, previous.path))
			}
			profiles[id] = doc
			if strings.TrimSuffix(path.Base(name), path.Ext(name)) != id {
				failures = append(failures, fmt.Errorf("%s: profile id differs from filename", name))
			}
		case Release:
			releases = append(releases, doc)
		}
		return nil
	})
	if err != nil {
		failures = append(failures, fmt.Errorf("scan repository: %w", err))
	}
	for _, release := range releases {
		if err := validateReferences(release.value, packs, profiles); err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", release.path, err))
		}
	}
	return errors.Join(failures...)
}

func repositoryKind(name string) Kind {
	base := path.Base(name)
	switch {
	case strings.HasPrefix(name, "models/") && (base == "pack.yaml" || base == "pack.yml"):
		return ModelPack
	case strings.HasPrefix(name, "profiles/") && (path.Ext(name) == ".yaml" || path.Ext(name) == ".yml"):
		return HardwareProfile
	case base == "release.json":
		return Release
	case strings.HasPrefix(name, "evidence/") && base == "run.json":
		return Evidence
	default:
		return ""
	}
}

func readRegular(root *os.Root, name string, limit int64) ([]byte, error) {
	info, err := root.Lstat(name)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("expected a regular file, not a symlink or device")
	}
	file, err := root.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err = file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("expected a regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("file exceeds size limit")
	}
	return data, nil
}

func validateReferences(release map[string]any, packs, profiles map[string]document) error {
	ref := release["model_pack"].(map[string]any)
	pack, ok := packs[ref["id"].(string)]
	if !ok {
		return errors.New("/model_pack/id: referenced model pack is missing or invalid")
	}
	if err := ValidateDeployableModelPack(pack.data); err != nil {
		return fmt.Errorf("/model_pack: referenced pack is an incomplete draft: %w", err)
	}
	if ref["verity_root_hash"] != pack.value["weights_volume"].(map[string]any)["verity_root_hash"] {
		return errors.New("/model_pack/verity_root_hash: differs from referenced pack")
	}
	engines := pack.value["engine_profiles"].(map[string]any)
	selected := make(map[string]any)
	for id := range release["profiles"].(map[string]any) {
		profile, ok := profiles[id]
		if !ok {
			return errors.New("/profiles: referenced hardware profile is missing or invalid")
		}
		if profile.value["cc_mode"] == "none" {
			return errors.New("/profiles: release cannot accept a non-confidential profile")
		}
		engine, ok := engines[id].(map[string]any)
		if !ok {
			return errors.New("/profiles: model pack has no matching engine profile")
		}
		if engine["image"] != release["images"].(map[string]any)["engine"] {
			return errors.New("/images/engine: differs from referenced model pack engine")
		}
		selected[id] = engine
	}
	// Cross-document CC context comes from the hardware profiles, not a naming
	// convention. Check only released profiles; plain P2 comparisons may coexist.
	confidential := make(map[string]any, len(pack.value))
	for key, value := range pack.value {
		confidential[key] = value
	}
	confidential["engine_profiles"] = selected
	all, err := compileSchemas()
	if err != nil {
		return err
	}
	if err := validateValue(all["model-pack-confidential"], confidential); err != nil {
		return fmt.Errorf("/model_pack: confidential profile requirements: %w", err)
	}
	return nil
}

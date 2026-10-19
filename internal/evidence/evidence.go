// SPDX-License-Identifier: Apache-2.0

// Package evidence creates unexecuted evidence scaffolds and scrubs explicitly
// supplied operational output. It never executes or captures a workload.
package evidence

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const commandPlaceholder = "NOT RECORDED: no command has been executed."

var (
	phasePattern = regexp.MustCompile(`^[Pp][0-9]$`)
	slugPattern  = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)
	shaPattern   = regexp.MustCompile(`^[0-9a-f]{40}$`)
)

type scaffold struct {
	Schema            string  `json:"schema"`
	Phase             string  `json:"phase"`
	Task              *string `json:"task"`
	GitSHA            string  `json:"git_sha"`
	Command           string  `json:"command"`
	StartedUTC        *string `json:"started_utc"`
	EndedUTC          *string `json:"ended_utc"`
	Host              any     `json:"host"`
	Result            string  `json:"result"`
	Execution         string  `json:"execution"`
	HardwareValidated bool    `json:"hardware_validated"`
	Notes             string  `json:"notes"`
}

// New creates evidence/pN/YYYY-MM-DD-slug/{run.json,output.log} below the
// supplied Git worktree root and returns its repository-relative directory.
// The UTC date labels creation, not execution. Existing runs are never changed.
// Phase accepts P0..P9 (case-insensitive); slug is at most 64 lowercase ASCII
// letters/digits with single internal hyphens. Identifiers must not be secrets.
// Errors deliberately omit paths, arguments, Git output and underlying errors.
func New(root, phase, slug string) (string, error) {
	return newAt(root, phase, slug, time.Now())
}

func newAt(root, phase, slug string, now time.Time) (string, error) {
	if !phasePattern.MatchString(phase) || len(slug) > 64 || !slugPattern.MatchString(slug) {
		return "", errors.New("evidence: invalid phase or slug")
	}
	gitRoot, err := repositoryRoot(root)
	if err != nil {
		return "", err
	}
	a, errA := os.Stat(root)
	b, errB := os.Stat(gitRoot)
	if errA != nil || errB != nil || !os.SameFile(a, b) {
		return "", errors.New("evidence: expected a Git worktree root")
	}
	sha, err := gitValue(root, "--verify", "HEAD")
	if err != nil || !shaPattern.MatchString(sha) {
		return "", errors.New("evidence: cannot identify the committed Git revision")
	}
	repo, err := os.OpenRoot(root)
	if err != nil {
		return "", errors.New("evidence: cannot open repository")
	}
	defer repo.Close()

	// Root operations provide traversal-resistant resolution, including during
	// symlink replacement races: https://go.dev/blog/osroot
	evidence, err := openDirectory(repo, "evidence", false)
	if err != nil {
		return "", err
	}
	defer evidence.Close()
	phase = strings.ToLower(phase)
	phaseDir, err := openDirectory(evidence, phase, false)
	if err != nil {
		return "", err
	}
	defer phaseDir.Close()
	name := now.UTC().Format("2006-01-02") + "-" + slug
	runDir, err := openDirectory(phaseDir, name, true)
	if err != nil {
		return "", err
	}
	defer runDir.Close()

	run := scaffold{
		Schema: "nib.evidence/v1", Phase: strings.ToUpper(phase), GitSHA: sha,
		Command: commandPlaceholder, Result: "not_run", Execution: "not_run",
		Notes: "Scaffold only; no command executed or hardware validated.",
	}
	data, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return "", errors.New("evidence: cannot encode scaffold")
	}
	if err := writePrivate(runDir, "run.json", append(data, '\n')); err != nil {
		return "", err
	}
	if err := writePrivate(runDir, "output.log", nil); err != nil {
		return "", err
	}
	return filepath.ToSlash(filepath.Join("evidence", phase, name)), nil
}

func openDirectory(parent *os.Root, name string, exclusive bool) (*os.Root, error) {
	err := parent.Mkdir(name, 0o700)
	if err != nil && (exclusive || !errors.Is(err, os.ErrExist)) {
		return nil, errors.New("evidence: cannot create directory; existing runs are never overwritten")
	}
	info, err := parent.Lstat(name)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0o022 != 0 {
		return nil, errors.New("evidence: directory must be real and not writable by other users")
	}
	dir, err := parent.OpenRoot(name)
	if err != nil {
		return nil, errors.New("evidence: cannot open confined directory")
	}
	return dir, nil
}

func writePrivate(root *os.Root, name string, data []byte) error {
	f, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return errors.New("evidence: cannot create private file")
	}
	_, writeErr := f.Write(data)
	closeErr := f.Close()
	if writeErr != nil || closeErr != nil {
		return errors.New("evidence: cannot finish private file; inspect incomplete scaffold")
	}
	return nil
}

func repositoryRoot(dir string) (string, error) {
	root, err := gitValue(dir, "--show-toplevel")
	if err != nil || root == "" {
		return "", errors.New("evidence: cannot locate Git worktree")
	}
	return root, nil
}

func gitValue(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", dir, "rev-parse"}, args...)...)
	// Ignore ambient Git overrides so another repository cannot supply the SHA.
	for _, env := range os.Environ() {
		if !strings.HasPrefix(env, "GIT_") {
			cmd.Env = append(cmd.Env, env)
		}
	}
	cmd.Env = append(cmd.Env, "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL="+os.DevNull)
	cmd.Stderr = io.Discard
	out, err := cmd.Output()
	if err != nil {
		return "", errors.New("evidence: Git metadata unavailable")
	}
	return strings.TrimSuffix(string(out), "\n"), nil
}

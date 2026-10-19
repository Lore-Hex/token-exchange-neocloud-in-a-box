// SPDX-License-Identifier: Apache-2.0
package evidence

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var testTime = time.Date(2026, 10, 19, 1, 2, 3, 0, time.FixedZone("test", 14*60*60))

func testRepo(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	testGit(t, root, "init", "--quiet")
	testGit(t, root, "-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "synthetic test fixture")
	return root
}

func testGit(t *testing.T, root string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", root}, args...)...)
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + root, "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=" + os.DevNull}
	out, err := cmd.Output()
	if err != nil {
		t.Fatal("temporary Git fixture command failed")
	}
	return strings.TrimSpace(string(out))
}

func TestNewHonestPrivateScaffold(t *testing.T) {
	root := testRepo(t)
	t.Setenv("NIB_TEST_CREDENTIAL", "private-environment-canary")
	dir, err := newAt(root, "P0", "unit-test", testTime)
	if err != nil {
		t.Fatal(err)
	}
	if dir != "evidence/p0/2026-10-18-unit-test" {
		t.Fatalf("incorrect UTC creation directory: %s", dir)
	}
	data, err := os.ReadFile(filepath.Join(root, dir, "run.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"schema": "nib.evidence/v1", "phase": "P0", "task": nil,
		"git_sha": testGit(t, root, "rev-parse", "HEAD"), "command": commandPlaceholder,
		"started_utc": nil, "ended_utc": nil, "host": nil,
		"result": "not_run", "execution": "not_run", "hardware_validated": false,
		"notes": "Scaffold only; no command executed or hardware validated.",
	}
	if len(got) != len(want) {
		t.Fatal("unexpected scaffold fields")
	}
	for key, value := range want {
		if got[key] != value {
			t.Errorf("incorrect scaffold field %s", key)
		}
	}
	if bytes.Contains(data, []byte(os.Getenv("NIB_TEST_CREDENTIAL"))) {
		t.Fatal("environment credential leaked into scaffold")
	}
	for _, name := range []string{"evidence", "evidence/p0", dir} {
		info, err := os.Stat(filepath.Join(root, name))
		if err != nil || info.Mode().Perm()&0o077 != 0 {
			t.Fatal("scaffold directory is not private")
		}
	}
	for _, name := range []string{"run.json", "output.log"} {
		info, err := os.Stat(filepath.Join(root, dir, name))
		if err != nil || info.Mode().Perm()&0o077 != 0 {
			t.Fatal("scaffold file is not private")
		}
		if name == "output.log" && info.Size() != 0 {
			t.Fatal("unexecuted output must be empty")
		}
	}
}

func TestNewRefusesOverwrite(t *testing.T) {
	root := testRepo(t)
	dir, err := newAt(root, "p0", "same", testTime)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, dir, "run.json")
	if err := os.WriteFile(path, []byte("existing-evidence"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := newAt(root, "P0", "same", testTime); err == nil {
		t.Fatal("existing run accepted")
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "existing-evidence" {
		t.Fatal("existing run was modified")
	}
}

func TestNewRejectsTraversalAndSensitiveArguments(t *testing.T) {
	for _, phase := range []string{"", "P10", "0", "../p0", "P0/private-canary", "P0\n"} {
		if _, err := New("nonexistent", phase, "safe"); err == nil || strings.Contains(err.Error(), "private-canary") {
			t.Fatal("invalid phase accepted or echoed")
		}
	}
	for _, slug := range []string{"", ".", "..", "../outside", "a/b", `a\b`, "/absolute", "two words", "UPPER", "-start", "end-", "two--hyphens", "x\n", "x\x00", strings.Repeat("x", 65)} {
		if _, err := New("nonexistent", "P0", slug); err == nil {
			t.Fatal("invalid slug accepted")
		}
	}
}

func TestNewRejectsSymlinkComponents(t *testing.T) {
	for _, component := range []string{"evidence", "evidence/p0", "evidence/p0/2026-10-18-test"} {
		t.Run(component, func(t *testing.T) {
			root := testRepo(t)
			outside := t.TempDir()
			if err := os.MkdirAll(filepath.Dir(filepath.Join(root, component)), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, filepath.Join(root, component)); err != nil {
				t.Fatal(err)
			}
			if _, err := newAt(root, "P0", "test", testTime); err == nil {
				t.Fatal("symlink accepted")
			}
			entries, err := os.ReadDir(outside)
			if err != nil || len(entries) != 0 {
				t.Fatal("wrote outside repository")
			}
		})
	}
}

func TestNewRejectsSymlinkFilesInExistingRun(t *testing.T) {
	root := testRepo(t)
	dir := filepath.Join(root, "evidence/p0/2026-10-18-test")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "untouched")
	if err := os.WriteFile(outside, []byte("unchanged"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"run.json", "output.log"} {
		if err := os.Symlink(outside, filepath.Join(dir, name)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := newAt(root, "P0", "test", testTime); err == nil {
		t.Fatal("existing symlink run accepted")
	}
	data, err := os.ReadFile(outside)
	if err != nil || string(data) != "unchanged" {
		t.Fatal("outside file changed")
	}
}

func TestNewChecksRootAndPermissions(t *testing.T) {
	root := testRepo(t)
	child := filepath.Join(root, "child")
	if err := os.Mkdir(child, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{t.TempDir(), child} {
		if _, err := New(path, "P0", "test"); err == nil {
			t.Fatal("non-root accepted")
		}
	}
	path := filepath.Join(root, "evidence")
	if err := os.Mkdir(path, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0o777); err != nil {
		t.Fatal(err)
	}
	if _, err := New(root, "P0", "test"); err == nil {
		t.Fatal("world-writable evidence directory accepted")
	}
}

func TestNewIgnoresAmbientGitOverrides(t *testing.T) {
	root := testRepo(t)
	t.Setenv("GIT_DIR", "/missing/private-git-canary")
	t.Setenv("GIT_WORK_TREE", "/missing/private-git-canary")
	t.Setenv("GIT_CONFIG_COUNT", "99")
	if _, err := New(root, "P0", "safe"); err != nil {
		t.Fatal(err)
	}
}

func TestNewRequiresCommittedRevision(t *testing.T) {
	root := t.TempDir()
	testGit(t, root, "init", "--quiet")
	if _, err := New(root, "P0", "test"); err == nil {
		t.Fatal("unborn repository accepted")
	}
	if _, err := os.Lstat(filepath.Join(root, "evidence")); !os.IsNotExist(err) {
		t.Fatal("failed metadata check wrote an evidence directory")
	}
}

func TestNewRejectsInternalSymlinkAndFileCollision(t *testing.T) {
	for _, symlink := range []bool{false, true} {
		root := testRepo(t)
		path := filepath.Join(root, "evidence")
		if symlink {
			if err := os.Mkdir(filepath.Join(root, "other"), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink("other", path); err != nil {
				t.Fatal(err)
			}
		} else if err := os.WriteFile(path, []byte("unchanged"), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := New(root, "P0", "test"); err == nil {
			t.Fatal("non-directory evidence component accepted")
		}
	}
}

func TestPrivateWriterNeverOverwrites(t *testing.T) {
	path := t.TempDir()
	root, err := os.OpenRoot(path)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	if err := writePrivate(root, "existing", []byte("original")); err != nil {
		t.Fatal(err)
	}
	if err := writePrivate(root, "existing", []byte("replacement")); err == nil {
		t.Fatal("existing file overwritten")
	}
	if err := os.Symlink("existing", filepath.Join(path, "link")); err != nil {
		t.Fatal(err)
	}
	if err := writePrivate(root, "link", []byte("replacement")); err == nil {
		t.Fatal("symlink file accepted")
	}
	data, err := os.ReadFile(filepath.Join(path, "existing"))
	if err != nil || string(data) != "original" {
		t.Fatal("existing file changed")
	}
}

func TestNewConcurrentCreateOnly(t *testing.T) {
	root := testRepo(t)
	var succeeded atomic.Int32
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := newAt(root, "P0", "concurrent", testTime); err == nil {
				succeeded.Add(1)
			}
		}()
	}
	wg.Wait()
	if succeeded.Load() != 1 {
		t.Fatal("expected exactly one create-only winner")
	}
}

# SPDX-License-Identifier: Apache-2.0
"""Original pre-container failure and offline restrictive-umask reproduction."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
REVISION = "84bec7335c9b5fc3a7d22408bf592f76c74c8310"
DIRECTORY = ROOT / "evidence/p2/2026-10-23-prefix-preparation-failure"
HASHES = {
    "pending.json": "177426b8569dc9de5b927c61d70a78fd78d1bbf45931a9bbd19868b92abfbe2e",
    "render/run.json": "a672e567867d41720517b3c45050e1be4768fc884885eecf15b702d6784e6a60",
    "render/output.log": "ec29219f4872b2ad66547e86ea0db1d5db6a5a6b701a6eebc2d9c66c3eb5ba99",
    "result/run.json": "d928cf09d122f15c849c79cd1ba753213b0aa6e6282e3fcf7bc4b18977d70a54",
    "result/output.log": "e63208de8ebf526b7586564722f23af4778901353ded78c92e3895ddcb07cd14",
}


class PrefixPreparationEvidenceTests(unittest.TestCase):
    def test_active_host_builder_pin_loads_current_committed_bootstrap(self):
        path = ROOT / "host/record-deepseek-lab.py"
        host = types.ModuleType("active_host_builder_check")
        host.__file__ = str(path)
        exec(compile(path.read_bytes(), str(path), "exec"), host.__dict__)
        source = git(ROOT, "show", "HEAD:" + host.BUILDER)
        self.assertEqual((ROOT / host.BUILDER).read_bytes(), source)
        self.assertEqual(host.BUILDER_SHA, hashlib.sha256(source).hexdigest())
        builder = host.load("active_committed_builder", ROOT / host.BUILDER, host.BUILDER_SHA)
        self.assertEqual(builder.BOOTSTRAP, source)
        self.assertEqual(builder.recorder.prep.CHECKER_SOURCE, (ROOT / "scripts/check-engine-candidate.py").read_bytes())

    def test_original_bytes_and_pre_container_failure_are_preserved(self):
        self.assertEqual({p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob("*") if p.is_file()},
                         set(HASHES))
        rows = {}
        for name, digest in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            rows[name] = json.loads(raw)
        pending, render, final = (rows[name] for name in ("pending.json", "render/output.log", "result/output.log"))
        self.assertEqual((pending["git_sha"], pending["result"], pending["cleanup"]),
                         (REVISION, "incomplete", "unproven"))
        self.assertEqual((final["git_sha"], final["stage"], final["result"]),
                         (REVISION, "tokenizer-inputs", "fail"))
        self.assertEqual((final["started_utc"], final["ended_utc"]),
                         ("2026-10-23T03:19:09Z", "2026-10-23T03:19:18Z"))
        self.assertEqual(final["render"], render)
        self.assertEqual((render["stage"], render["result"], render["cleanup"]),
                         ("completed", "pass", "confirmed"))
        self.assertEqual(render["scorer_image_profile"], "docker29-descriptor")
        self.assertIsNone(final["tokenizer"])
        self.assertIsNone(final["observation"])
        prior = json.loads((ROOT / "evidence/p2/2026-10-23-tau-agent-prefix-local-fb41c77/render/output.log").read_bytes())
        self.assertEqual(render["prefix"], prior["prefix"])
        for row in (render, final):
            for flag in ("quality_eligible", "confidential", "full_fit_proven"):
                self.assertIs(row[flag], False)
        self.assertIs(final["generation_performed"], False)
        for prefix, result in (("render", "pass"), ("result", "fail")):
            run = rows[prefix + "/run.json"]
            self.assertEqual((run["git_sha"], run["result"], run["execution"]), (REVISION, result, "software"))
            self.assertEqual(run["host"]["gpus"], "none")
            self.assertIs(run["hardware_validated"], False)

    def test_original_scorer_source_closure_is_exact(self):
        render = json.loads((DIRECTORY / "render/output.log").read_bytes())
        toolkit = render["toolkit"]
        self.assertEqual(toolkit["git_sha"], REVISION)
        self.assertEqual(len(toolkit["files"]), 22)
        for name, digest in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":" + name)).hexdigest(), digest)

    def test_original_exact_checker_reproduces_mode_only_failure_without_private_request(self):
        source = r'''
import json, os, stat, sys, types
from pathlib import Path
root, revision = Path(sys.argv[1]), sys.argv[2]
checker = types.ModuleType("historical_checker")
checker.__file__ = str(root / "scripts/check-engine-candidate.py")
exec(compile(sys.stdin.buffer.read(), checker.__file__, "exec"), checker.__dict__)
original_git = checker.git
def historical_git(repo, *args, **kwargs):
    if args == ("rev-parse", "--verify", "HEAD^{commit}"):
        return (revision + "\n").encode()
    return original_git(repo, *args, **kwargs)
checker.git = historical_git
_, bundle = checker.committed_bundle(root)
manifest, _ = checker.metadata(bundle)
observed = []
def observed_git(repo, *args, **kwargs):
    result = original_git(repo, *args, **kwargs)
    if args[0] == "apply" and "--check" not in args:
        for item in manifest["files"]:
            path = Path(repo) / item["path"]
            assert checker.sha256(checker.read_regular(path)) == item["sha256"][1]
            if stat.S_IMODE(path.stat().st_mode) != int(item["mode"], 8) & 0o777:
                assert stat.S_IMODE(path.stat().st_mode) == (int(item["mode"], 8) & 0o777 & ~0o077)
                observed.append(stat.S_IMODE(path.stat().st_mode))
    return result
checker.git = observed_git
os.umask(0o077)
try:
    checker.verify_stages(bundle)
except ValueError:
    assert set(observed) == {0o600, 0o700}
else:
    raise AssertionError("historical restrictive mask unexpectedly accepted")
assert os.umask(0o077) == 0o077
print(json.dumps({"hashes_match": True, "mode_only_failure": True, "parent_umask": 0o077}))
'''
        original = git(ROOT, "show", REVISION + ":scripts/check-engine-candidate.py")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", source, str(ROOT), REVISION],
                                input=original, capture_output=True, timeout=60, check=True)
        self.assertEqual(json.loads(result.stdout),
                         {"hashes_match": True, "mode_only_failure": True, "parent_umask": 0o077})
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()

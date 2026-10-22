# SPDX-License-Identifier: Apache-2.0
"""Nonexecuting synthetic source/package fixtures; no upstream runtime imports."""
import copy
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    value = types.ModuleType(name)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), value.__dict__)
    return value


tool = module("mkosi_test_tool", ROOT / "scripts/prepare_mkosi_inputs.py")
fixtures = module("mkosi_deb_fixtures", ROOT / "scripts/test_prepare_guest_packages.py")
PATCH = (ROOT / tool.EXTRA[3]).read_bytes()
PATCH_PIN = json.loads((ROOT / tool.EXTRA[4]).read_bytes())


def synthetic_installer():
    # Only this reviewed synthetic method runs. No upstream module is imported.
    prefix = (b"# SPDX-License-Identifier: LGPL-2.1-or-later\nclass Installer:\n"
              b"    @classmethod\n"
              b"    def invoke(cls, context, operation, arguments=(), apivfs=False, options=(), stdout=None):\n"
              + tool.package_patch.BEFORE +
              b"    @classmethod\n    def install(cls, context, allow_downgrade=False):\n")
    prefix += b"\n" * (258 - len(prefix.splitlines()))
    hunk = PATCH.splitlines(keepends=True)[3:]
    before = b"".join(line[1:] for line in hunk if line.startswith(b" "))
    after = b"".join(line[1:] for line in hunk)
    tail = (b"\n        arguments = []\n\n        if allow_downgrade:\n"
            b"            arguments += ['--allow-downgrades']\n"
            b"        cls.invoke(context, 'install', arguments, apivfs=True)\n"
            b"        policyrcd.unlink()\n")
    original, result = prefix + before + tail, prefix + after + tail
    pin = {**PATCH_PIN, "input_bytes": len(original), "input_sha256": tool.base.digest(original),
           "result_bytes": len(result), "result_sha256": tool.base.digest(result)}
    return original, result, pin


@contextmanager
def fixture_umask(value):
    old = os.umask(value)
    try:
        yield
    finally:
        os.umask(old)


def source(entries=None, transform=None):
    entries = entries or [
        ("pyproject.toml", b'[project]\nversion="27.1"\n', "100644"),
        ("mkosi/__main__.py", b'raise RuntimeError("must not execute upstream")\n', "100644"),
        ("LICENSES/LGPL-2.1-or-later.txt", b"synthetic license fixture\n", "100644"),
        ("entry", b"mkosi/__main__.py", "120000"),
        (tool.PATCH_TARGET, synthetic_installer()[0], "100644"),
    ]
    prefix = "mkosi-" + tool.COMMIT + "/"
    pin = {"schema": "nib.mkosi-source/v1", "version": "27.1", "git_sha": tool.COMMIT,
           "repository": "https://github.com/systemd/mkosi",
           "archive_url": "https://codeload.github.com/systemd/mkosi/tar.gz/" + tool.COMMIT,
           "archive_prefix": prefix, "required_python": ">=3.9",
           "executed": False, "build_verified": False, "deployable": False,
           "source_files": {}, "license_files": sorted(n for n, _, _ in entries if n.startswith("LICENSES/"))}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, raw, mode in entries:
            pin["source_files"][name] = {
                "bytes": len(raw), "sha256": tool.base.digest(raw), "mode": mode,
                "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
            member = tarfile.TarInfo(prefix + name)
            member.mode = 0o775 if mode == "100755" else 0o664
            if mode == "120000":
                member.type, member.linkname = tarfile.SYMTYPE, raw.decode()
            else:
                member.size = len(raw)
            if transform:
                transform(member)
            archive.addfile(member, io.BytesIO(raw) if member.isfile() else None)
    raw = gzip.compress(buf.getvalue(), mtime=0)
    pin.update(archive_bytes=len(raw), archive_sha256=tool.base.digest(raw))
    return raw, pin


class SourceTests(unittest.TestCase):
    def test_complete_source_and_license_preserved_without_execution(self):
        raw, pin = source()
        result, members = tool.verify_source(raw, pin)
        self.assertEqual(result["files"], 5)
        self.assertEqual(result["source_bytes"], sum(r["bytes"] for r in pin["source_files"].values()))
        self.assertTrue(result["git_blob_correspondence"])
        self.assertTrue(result["license_files_preserved"])
        self.assertFalse(result["upstream_executed"])
        self.assertEqual(tool.verify_source(raw, pin), (result, members))

    def test_archive_and_every_source_hash_git_blob_size_and_mode_bound(self):
        raw, pin = source()
        for key, value in (("sha256", "0" * 64), ("git_blob", "0" * 40),
                           ("bytes", 1), ("mode", "100755")):
            bad = copy.deepcopy(pin)
            bad["source_files"]["mkosi/__main__.py"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.verify_source(raw, bad)
        with self.assertRaises(ValueError):
            tool.verify_source(raw + b"x", pin)
        for key, value in (("version", "latest"), ("git_sha", "0" * 40),
                           ("archive_url", "https://example.invalid/private"),
                           ("required_python", "any"), ("executed", True)):
            bad = {**pin, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.verify_source(raw, bad)

    def test_missing_extra_duplicate_license_and_unsafe_members_reject(self):
        raw, pin = source()
        bad = copy.deepcopy(pin)
        del bad["source_files"]["entry"]
        with self.assertRaises(ValueError):
            tool.verify_source(raw, bad)
        bad = copy.deepcopy(pin)
        bad["license_files"] = []
        with self.assertRaises(ValueError):
            tool.verify_source(raw, bad)
        entries = [("mkosi/__main__.py", b"never", "100644")] * 2
        raw, pin = source(entries)
        with self.assertRaises(ValueError):
            tool.verify_source(raw, pin)
        def change_name(m):
            m.name = "mkosi-" + tool.COMMIT + "/../private"
        def change_fifo(m):
            m.type, m.size = tarfile.FIFOTYPE, 0
        def change_hardlink(m):
            m.type, m.size, m.linkname = tarfile.LNKTYPE, 0, "private"
        def change_suid(m):
            m.mode = 0o4755
        def change_pax(m):
            m.pax_headers = {"path": "private"}
        for change in (change_name, change_fifo, change_hardlink, change_suid, change_pax):
            raw, pin = source(transform=change)
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                tool.verify_source(raw, pin)

    def test_source_links_cannot_escape_dangle_or_cycle(self):
        for target in (b"/private/secret", b"../../private", b"missing", b"entry"):
            raw, pin = source([("entry", target, "120000")])
            with self.subTest(target=target), self.assertRaises(ValueError):
                tool.verify_source(raw, pin)

    def test_source_expansion_and_entry_count_are_bounded(self):
        raw, pin = source()
        with mock.patch.object(tool, "MAX_SOURCE", len(raw) + 1):
            with self.assertRaises(ValueError):
                tool.verify_source(raw, pin)
        many = [("entry" + str(n), b"x", "100644") for n in range(513)]
        raw, pin = source(many)
        with self.assertRaises(ValueError):
            tool.verify_source(raw, pin)

    def test_bootstrap_rejects_fifo_links_size_without_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "scripts").mkdir()
            path = root / "scripts/prepare_guest_tools.py"
            for kind in ("fifo", "symlink", "hardlink", "oversize"):
                other = root / "other"
                other.write_bytes(b"must not execute")
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "symlink":
                    path.symlink_to(other)
                elif kind == "hardlink":
                    os.link(other, path)
                else:
                    path.write_bytes(b"x" * (1 << 20))
                with mock.patch.object(tool, "ROOT", root), self.subTest(kind=kind):
                    with self.assertRaises((ValueError, OSError)):
                        tool.load()
                path.unlink()


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archives = self.root / "archives"
        self.metadata = self.root / "metadata"
        self.archives.mkdir()
        self.metadata.mkdir()
        self.raw, pin = source()
        self.deb = fixtures.deb()
        self.row = fixtures.row(self.deb)
        self.archive = self.archives / (self.row["SHA256"] + ".deb")
        self.archive.write_bytes(self.deb)
        self.index = b"synthetic public index"
        (self.metadata / "fixture.packages").write_bytes(self.index)
        self.lock = {"archive_bytes": len(self.deb), "packages": [self.row], "indexes": [
            {"file": "fixture.packages", "bytes": len(self.index), "sha256": tool.base.digest(self.index)}]}
        self.sources = {tool.EXTRA[0]: b"bound test code", tool.EXTRA[1]: tool.base.encoded(pin),
                        tool.EXTRA[2]: tool.base.encoded(self.lock),
                        tool.EXTRA[3]: PATCH, tool.EXTRA[4]: tool.base.encoded(synthetic_installer()[2]),
                        tool.tools.FILES[-1]: tool.base.encoded(self.lock)}
        fixed = synthetic_installer()[1]
        boundary = fixed.replace(tool.package_patch.BEFORE, tool.package_patch.replacement())
        boundary_pin = json.loads((ROOT / tool.EXTRA[6]).read_bytes())
        boundary_pin.update(input_bytes=len(fixed), input_sha256=tool.base.digest(fixed),
                            result_bytes=len(boundary), result_sha256=tool.base.digest(boundary))
        self.sources.update({tool.EXTRA[6]: tool.base.encoded(boundary_pin),
                             tool.EXTRA[7]: b"fixed worker", tool.EXTRA[8]: b"fixed guard"})

    def prepare(self):
        with (mock.patch.object(tool, "validate_tools_lock"), mock.patch.object(tool.base.inputs, "check"),
              mock.patch.object(tool, "validate_build_targets")):
            return tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources)

    def test_runtime_target_uses_only_explicit_lock_and_scope(self):
        path, scope = tool.tools.TARGETS["guest-runtime"]
        runtime = {**self.lock, "scope": scope}
        self.sources[path] = tool.base.encoded(runtime)
        with mock.patch.object(tool, "validate_tools_lock"), mock.patch.object(tool.base.inputs, "check") as check, \
                mock.patch.object(tool, "validate_build_targets"):
            ordinary, _ = tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources)
            check.assert_called_once_with(self.lock, self.metadata, scope=tool.tools.TARGETS["guest"][1])
            check.reset_mock()
            files, report = tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources,
                                          guest_runtime=True)
            check.assert_called_once_with(runtime, self.metadata, scope=scope)
            del self.sources[path]
            with self.assertRaises(KeyError):
                tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources, guest_runtime=True)
            with self.assertRaises(ValueError):
                tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources, guest_runtime="true")
        self.assertEqual(ordinary["guest-inputs.json"], self.sources[tool.tools.FILES[-1]])
        self.assertEqual(files["guest-inputs.json"], tool.base.encoded(runtime))
        self.assertEqual(files["mkosi.conf.candidate"], tool.base.recipe(runtime, skeleton_tree="mkosi-skeleton"))
        for name in ("packages_executed", "image_built", "bootable", "deployable"):
            self.assertFalse(report[name])

    def test_real_writer_verifier_deterministic_and_no_execution_claims(self):
        files, report = self.prepare()
        self.assertEqual((files, report), self.prepare())
        self.assertEqual(report["unique_archives"], 1)
        self.assertEqual(files["mkosi-source.tar.gz"], self.raw)
        self.assertEqual(files["mkosi-service-denial.patch"], PATCH)
        expected = tool.package_patch.apply(synthetic_installer()[1],
                                             json.loads(self.sources[tool.EXTRA[6]]))
        self.assertEqual(files["mkosi-overlay/" + tool.PATCH_TARGET], expected)
        self.assertEqual(files["nib/package-worker.py"], b"fixed worker")
        self.assertEqual(files["nib/confinement.py"], b"fixed guard")
        self.assertFalse(report["package_boundary_patch"]["executed"])
        self.assertEqual(report["local_patch"]["pin"], synthetic_installer()[2])
        self.assertIs(report["local_patch"]["executed"], False)
        self.assertEqual(files["repo/pool/" + tool.base.package_name(self.row)], self.deb)
        self.assertNotIn(b"exit 99", files["maintainer-inventory.json"])
        control = json.loads(files["maintainer-inventory.json"])["packages"][0]
        self.assertEqual(control["maintainer_members"], ["postinst"])
        self.assertEqual(control["control_members"]["postinst"]["sha256"], tool.base.digest(b"exit 99\n"))
        for key in ("execution_review_complete", "apt_rehearsal_passed", "upstream_executed",
                    "packages_executed", "image_built", "reproducibility_verified", "bootable", "deployable"):
            self.assertIs(report[key], False)
        contract = json.loads(files["build-contract.json"])
        self.assertIs(contract["execution_permitted"], False)
        self.assertIs(contract["bounds_runtime_validated"], False)
        self.assertEqual(contract["clean_builds_required"], 2)
        self.assertIn("matching-apt-rehearsal-pass", contract["required_before_execution"])
        self.assertEqual(files["mkosi.conf.candidate"],
                         tool.base.recipe(self.lock, skeleton_tree="mkosi-skeleton"))
        controls = contract["preinstall_controls"]
        self.assertFalse(controls["runtime_validated"])
        self.assertFalse(controls["automatic_dkms_builds"])
        self.assertFalse(controls["module_loading_on_install"])
        for name, raw in tool.dkms_build_controls().items():
            self.assertEqual(files[name], raw)
            self.assertEqual(controls["files"][name], tool.base.digest(raw))
        self.assertNotIn(str(self.root), json.dumps(report))
        destination = self.root / "output"
        tool.base.publish(destination, files)
        tool.base.verify(destination, files)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual(json.loads((destination / "receipt.json").read_bytes()), report)
        with self.assertRaises(FileExistsError):
            tool.base.publish(destination, files)
        for name in ("mkosi-service-denial.patch", "mkosi-service-denial.json",
                     "mkosi-overlay/" + tool.PATCH_TARGET, *tool.dkms_build_controls()):
            path = destination / name
            path.write_bytes(b"changed")
            with self.subTest(name=name), self.assertRaises(ValueError):
                tool.base.verify(destination, files)
            path.write_bytes(files[name])
        (destination / "mkosi-source.tar.gz").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            tool.base.verify(destination, files)

    def test_dkms_controls_pin_target_compiler_and_jobs_without_ignoring_mismatch(self):
        files = tool.dkms_build_controls()
        config = files["mkosi-skeleton/etc/dkms/nvidia-595.91.07-6.8.0-147-generic-x86_64.conf"]
        self.assertIn(b"'make' -j2 KERNEL_UNAME=6.8.0-147-generic", config)
        self.assertIn(b"SYSSRC=/lib/modules/6.8.0-147-generic/build", config)
        self.assertIn(b"CC=/usr/bin/x86_64-linux-gnu-gcc-13", config)
        self.assertIn(b"IGNORE_CC_MISMATCH= ", config)
        self.assertIn(b"IGNORE_PREEMPT_RT_PRESENCE= ", config)
        self.assertIn(b"IGNORE_XEN_PRESENCE= ", config)
        self.assertNotIn(b"=1", config)
        self.assertNotIn(b"nproc", config)
        self.assertNotIn(b"uname", config)
        self.assertNotIn(b"$", config)
        framework = files["mkosi-skeleton/etc/dkms/framework.conf.d/99-nib-build.conf"]
        # DKMS tests string non-emptiness, so 'false' would still load modules.
        self.assertIn(b'modprobe_on_install=""\n', framework)
        self.assertNotIn(b"try_sign_modules", framework)
        self.assertTrue(files["mkosi-skeleton/etc/dkms/no-autoinstall"])
        self.assertNotIn(b"SkeletonTrees", tool.base.recipe(self.lock))
        with self.assertRaises(ValueError):
            tool.base.recipe(self.lock, skeleton_tree="/arbitrary")

    def test_build_controls_require_the_reviewed_target_package_versions(self):
        guest = json.loads((ROOT / tool.tools.FILES[-1]).read_bytes())
        tool.validate_build_targets(guest)
        for name in ("dkms", "nvidia-dkms-open", "gcc-13", "gcc-13-x86-64-linux-gnu",
                     "linux-headers-6.8.0-147", "linux-headers-6.8.0-147-generic"):
            for mode in ("missing", "version", "duplicate"):
                bad = copy.deepcopy(guest)
                row = next(r for r in bad["packages"] if r["Package"] == name)
                if mode == "missing":
                    bad["packages"].remove(row)
                elif mode == "version":
                    row["Version"] = "new-unreviewed"
                else:
                    bad["packages"].append(row.copy())
                with self.subTest(name=name, mode=mode), self.assertRaises(ValueError):
                    tool.validate_build_targets(bad)

    def test_package_union_conflict_missing_hash_and_special_files_reject(self):
        bad = copy.deepcopy(self.lock)
        bad["packages"][0]["Version"] = "2.0"
        self.sources[tool.EXTRA[2]] = tool.base.encoded(bad)
        with self.assertRaises(ValueError):
            self.prepare()
        self.sources[tool.EXTRA[2]] = tool.base.encoded(self.lock)
        self.archive.unlink()
        for kind in ("missing", "changed", "fifo", "symlink", "hardlink"):
            if kind == "changed":
                self.archive.write_bytes(self.deb + b"x")
            elif kind == "fifo":
                os.mkfifo(self.archive)
            elif kind == "symlink":
                self.archive.symlink_to(self.metadata / "fixture.packages")
            elif kind == "hardlink":
                os.link(self.metadata / "fixture.packages", self.archive)
            with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                self.prepare()
            self.archive.unlink(missing_ok=True)

    def test_metadata_rechecked_and_unrelated_cache_bytes_never_read(self):
        # Shared download cache may contain other tasks' packages; bundle is exact.
        os.mkfifo(self.archives / "not-selected.deb")
        self.prepare()
        (self.metadata / "fixture.packages").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_input_validation_and_one_time_budget_precede_publication(self):
        with mock.patch.object(tool, "validate_tools_lock", side_effect=ValueError("fixed")):
            with self.assertRaises(ValueError):
                tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources)
        with mock.patch.object(tool.time, "monotonic", side_effect=[1000, 1601]):
            with self.assertRaises(ValueError):
                self.prepare()
        with mock.patch.object(tool, "MAX_BYTES", len(self.deb) - 1):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(set(self.root.iterdir()), {self.archives, self.metadata})

    def test_virtual_upstream_root_requires_exact_existing_provider(self):
        lock = {"provided_root_packages": {"systemd-repart": "systemd"},
                "upstream_requested_packages": ["systemd-repart", "systemd"],
                "roots": ["systemd"], "packages": [
                    {"Package": "systemd", "Version": "255.4", "Provides": "systemd-repart",
                     "Multi-Arch": "foreign"}]}
        with mock.patch.object(tool.base.inputs, "check") as check:
            tool.validate_tools_lock(lock, self.metadata)
            self.assertEqual(check.call_args.kwargs["scope"], "mkosi-build-tools-dependency-coverage")
            for mutate in ("missing", "unknown", "duplicate", "not-provider"):
                bad = copy.deepcopy(lock)
                if mutate == "missing":
                    bad["provided_root_packages"] = {}
                elif mutate == "unknown":
                    bad["upstream_requested_packages"].append("unknown")
                elif mutate == "duplicate":
                    bad["upstream_requested_packages"].append("systemd")
                else:
                    bad["packages"][0]["Provides"] = ""
                with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                    tool.validate_tools_lock(bad, self.metadata)


class BindingTests(unittest.TestCase):
    def test_extra_committed_inputs_are_exact_and_old_default_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = (*tool.tools.FILES, *tool.EXTRA)
            for name in paths:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            env = {**tool.base.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            git("init", "-q")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            with mock.patch.object(tool.tools, "ROOT", root):
                _, old = tool.tools.committed()
                _, new = tool.tools.committed(tool.EXTRA)
                self.assertEqual(set(old), set(tool.tools.FILES))
                self.assertEqual(set(new), set(paths))
                for extra in (("../private",), ("/private",), ("-option",), ["not-tuple"],
                              (tool.EXTRA[0],) * 30):
                    with self.subTest(extra=extra), self.assertRaises(ValueError):
                        tool.tools.committed(extra)
                (root / tool.EXTRA[1]).write_bytes(b"uncommitted")
                with self.assertRaises(ValueError):
                    tool.tools.committed(tool.EXTRA)
                (root / tool.EXTRA[1]).write_bytes((ROOT / tool.EXTRA[1]).read_bytes())
                for name in tool.EXTRA[3:]:
                    path = root / name
                    path.write_bytes(b"uncommitted")
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        tool.tools.committed(tool.EXTRA)
                    path.write_bytes((ROOT / name).read_bytes())


class ServiceDenialTests(unittest.TestCase):
    def setUp(self):
        self.original, self.expected, self.pin = synthetic_installer()
        self.members = {tool.PATCH_TARGET: self.original}
        self.source_pin = {"source_files": {tool.PATCH_TARGET: {
            "bytes": len(self.original), "sha256": tool.base.digest(self.original), "mode": "100644"}}}

    def apply(self, patch=PATCH, pin=None):
        return tool.service_denial_patch(self.members, self.source_pin, patch, pin or self.pin)

    def installer(self, raw):
        namespace = {"umask": fixture_umask}
        exec(compile(raw, "synthetic-service-denial", "exec", dont_inherit=True), namespace)
        return namespace["Installer"]

    def test_patch_metadata_binds_actual_upstream_manifest_and_license(self):
        source_pin = json.loads((ROOT / tool.EXTRA[1]).read_bytes())
        row = source_pin["source_files"][tool.PATCH_TARGET]
        self.assertEqual((row["bytes"], row["sha256"]),
                         (PATCH_PIN["input_bytes"], PATCH_PIN["input_sha256"]))
        self.assertEqual((len(PATCH), tool.base.digest(PATCH)),
                         (PATCH_PIN["patch_bytes"], PATCH_PIN["patch_sha256"]))
        self.assertEqual(PATCH_PIN["mkosi_git_sha"], source_pin["git_sha"])
        self.assertEqual(PATCH_PIN["license"], "LGPL-2.1-or-later")

    def test_exact_insert_preserves_every_other_byte_and_source_objects(self):
        saved = copy.deepcopy((self.members, self.source_pin, self.pin))
        result = self.apply()
        self.assertEqual(result, self.expected)
        self.assertEqual(result.replace(b"        policyrcd.chmod(0o755)\n", b""), self.original)
        self.assertEqual((self.members, self.source_pin, self.pin), saved)

    def test_absent_and_existing_modes_deny_before_each_transaction(self):
        installer = self.installer(self.apply())
        for prior in (None, 0o644, 0o755, 0o777):
            with self.subTest(prior=prior), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                policy = root / "usr/sbin/policy-rc.d"
                if prior is not None:
                    policy.parent.mkdir(parents=True)
                    policy.write_bytes(b"old policy must be replaced")
                    policy.chmod(prior)
                def invoke(context, action, arguments, *, apivfs):
                    self.assertEqual((action, arguments, apivfs), ("install", [], True))
                    self.assertEqual(policy.read_bytes(), b"#!/bin/sh\nexit 101\n")
                    self.assertEqual(stat.S_IMODE(policy.stat().st_mode), 0o755)
                with mock.patch.object(installer, "invoke", create=True, side_effect=invoke) as call:
                    with fixture_umask(0o077):
                        installer.install(types.SimpleNamespace(root=root))
                        self.assertFalse(policy.exists())
                        installer.install(types.SimpleNamespace(root=root))
                    self.assertEqual(call.call_count, 2)
                self.assertFalse(policy.exists())

    def test_original_synthetic_control_is_nonexecutable(self):
        installer = self.installer(self.original)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def invoke(*args, **kwargs):
                self.assertEqual(stat.S_IMODE((root / "usr/sbin/policy-rc.d").stat().st_mode), 0o644)
            with mock.patch.object(installer, "invoke", create=True, side_effect=invoke):
                installer.install(types.SimpleNamespace(root=root))

    def test_chmod_error_never_invokes_apt_and_failed_transaction_retry_denies(self):
        installer = self.installer(self.apply())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context = types.SimpleNamespace(root=root)
            with mock.patch.object(installer, "invoke", create=True) as call:
                with mock.patch.object(Path, "chmod", side_effect=OSError("synthetic")):
                    with self.assertRaises(OSError):
                        installer.install(context)
                call.assert_not_called()
            with mock.patch.object(installer, "invoke", create=True, side_effect=RuntimeError("synthetic")):
                with self.assertRaises(RuntimeError):
                    installer.install(context)
            policy = root / "usr/sbin/policy-rc.d"
            policy.chmod(0o644)
            def invoke(*args, **kwargs):
                self.assertEqual(stat.S_IMODE(policy.stat().st_mode), 0o755)
                self.assertEqual(policy.read_bytes(), b"#!/bin/sh\nexit 101\n")
            with mock.patch.object(installer, "invoke", create=True, side_effect=invoke):
                installer.install(context)
            self.assertFalse(policy.exists())

    def test_tampered_pin_patch_input_output_or_license_reject(self):
        for key, value in (("input_sha256", "0" * 64), ("result_sha256", "0" * 64),
                           ("patch_sha256", "0" * 64), ("input_bytes", 1), ("result_bytes", 1),
                           ("patch_bytes", 1), ("mkosi_git_sha", "0" * 40),
                           ("license", "Apache-2.0"), ("source_path", "other.py"),
                           ("patch_path", "../other.patch"), ("extra", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply(pin={**self.pin, key: value})
        with self.assertRaises(ValueError):
            self.apply(patch=PATCH + b"x")
        self.members[tool.PATCH_TARGET] += b"x"
        with self.assertRaises(ValueError):
            self.apply()

    def test_no_fuzzy_offset_reapply_or_arbitrary_hunks(self):
        for mutation in (b"\n" + self.original, self.expected,
                         self.original.replace(b'"usr/sbin/policy-rc.d"', b'"usr/sbin/other"')):
            self.members[tool.PATCH_TARGET] = mutation
            self.source_pin["source_files"][tool.PATCH_TARGET].update(
                bytes=len(mutation), sha256=tool.base.digest(mutation))
            pin = {**self.pin, "input_bytes": len(mutation), "input_sha256": tool.base.digest(mutation)}
            with self.subTest(mutation=len(mutation)), self.assertRaises(ValueError):
                self.apply(pin=pin)
        self.setUp()
        for patch in (PATCH.replace(b"@@ -259,5 +259,6 @@", b"@@ -258,5 +258,6 @@"),
                      PATCH.replace(b"chmod(0o755)", b"chmod(0o777)"), PATCH + b"@@ extra\n"):
            pin = {**self.pin, "patch_bytes": len(patch), "patch_sha256": tool.base.digest(patch)}
            with self.subTest(patch=len(patch)), self.assertRaises(ValueError):
                self.apply(patch=patch, pin=pin)


if __name__ == "__main__":
    unittest.main()

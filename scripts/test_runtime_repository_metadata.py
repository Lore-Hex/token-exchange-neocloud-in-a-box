# SPDX-License-Identifier: Apache-2.0
"""Synthetic composition only: no host inventory, transport or verifier process."""
import ast
from datetime import datetime, timezone
from email.parser import Parser
from email.utils import format_datetime
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "host/inspect-runtime-repository-metadata.py"
module = types.SimpleNamespace(**runpy.run_path(str(SOURCE)))
NOW = 1800000000
SIGNER = "A" * 40
SECRET = "DO_NOT_PRINT_SYNTHETIC_SECRET"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def date(value):
    return format_datetime(datetime.fromtimestamp(value, timezone.utc), usegmt=True)


def stanza(fields):
    return ("\n".join(key + ": " + value for key, value in fields) + "\n\n").encode()


def checksum(path, raw):
    return " %s %d %s" % (digest(raw), len(raw), path)


def binary(name):
    expression = "libc6 (>= 2.38)"
    if name != "libsystemd-shared":
        expression = "libsystemd-shared (= " + module.VERSION + "), " + expression
    return stanza((("Package", name), ("Version", module.VERSION), ("Architecture", "amd64"),
        ("Source", "systemd (" + module.VERSION + ")"),
        ("Filename", "pool/main/s/systemd/" + name + "_" + module.VERSION + "_amd64.deb"),
        ("Size", "100"), ("SHA256", digest(name.encode())), ("Depends", expression)))


class Clock:
    def __init__(self):
        self.monotonic = 10.0
        self.wall = NOW

    def __call__(self):
        return self.monotonic

    def utc(self):
        return self.wall


class Stream:
    def __init__(self, raw, fixture):
        self.raw, self.fixture, self.offset = raw, fixture, 0
        self.closed = False
        fixture.streams.append(self)

    def read(self, count):
        if self.closed:
            raise AssertionError("closed synthetic stream")
        part = self.raw[self.offset:self.offset + min(count, self.fixture.chunk)]
        self.offset += len(part)
        self.fixture.reads.append((count, len(part)))
        self.fixture.after_read()
        return part

    def close(self):
        self.closed = True


class Response:
    def __init__(self, fixture, url, raw):
        self.fixture, self.url = fixture, url
        self.status, self.redirected = fixture.http_status, False
        header = b"Content-Length: " + str(len(raw)).encode() + b"\r\n\r\n"
        self.headers = Stream(fixture.headers if fixture.headers is not None else header, fixture)
        self.body = Stream(raw, fixture)
        if fixture.wrong_url:
            self.url += "?" + SECRET
        self.redirected = fixture.redirected

    def close(self):
        self.headers.close()
        self.body.close()
        self.fixture.after_close()


class Verification:
    def __init__(self, fixture, pocket):
        self.fixture = fixture
        self.text = Stream(fixture.texts[pocket], fixture)
        self.status = Stream(fixture.status, fixture)
        self.diagnostics = Stream(fixture.diagnostics, fixture)
        self.exited = fixture.exited

    def finish(self, deadline):
        self.fixture.deadlines.append(deadline)
        self.fixture.after_finish()
        return self.fixture.exit_status

    def close(self):
        self.text.close()
        self.status.close()
        self.diagnostics.close()
        self.fixture.after_close()


class Fixture:
    """All bytes and lifecycle facts here are fabricated, not repository evidence."""
    def __init__(self):
        self.clock, self.chunk = Clock(), 97
        self.objects, self.texts, self.index_data = {}, {}, {}
        self.calls, self.reads, self.streams, self.deadlines = [], [], [], []
        self.verified, self.cleanups = [], []
        self.http_status, self.exit_status, self.exited = 200, 0, True
        self.headers, self.wrong_url, self.redirected = None, False, False
        self.status = ("[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG 12345678 " + SECRET + "\n"
            "[GNUPG:] VALIDSIG " + SIGNER + " 2027-01-15 " + str(NOW) +
            " 0 4 0 1 8 01 " + SIGNER + "\n").encode()
        self.diagnostics = SECRET.encode()
        self.after_read = self.after_close = self.after_finish = self.after_cleanup = lambda: None
        self.trust = {"keyring_sha256": digest(b"synthetic-keyring-not-a-real-key"),
                      "signers": (SIGNER,), "key_acceptable": True}
        self.baselines = {p: {"date": NOW - 100, "inrelease_sha256": digest(b"synthetic-baseline")}
                          for p in module.POCKETS}
        for pocket in module.POCKETS:
            self.index_data[pocket] = {
                module.INDEXES[0]: b"".join(binary(name) for name in module.PACKAGES),
                module.INDEXES[1]: stanza((("Package", "systemd"), ("Version", module.VERSION),
                    ("Directory", "pool/main/s/systemd"), ("Binary", ", ".join(module.PACKAGES)),
                    ("Checksums-Sha256", "\n" + "\n".join(checksum(name, b"synthetic-source-" + name.encode())
                                                       for name in module.SOURCE_FILES)))),
            }
            self.rebuild(pocket)

    def rebuild(self, pocket, dated=NOW - 10, valid_until=None):
        checks = []
        for path, raw in self.index_data[pocket].items():
            packed = lzma.compress(raw, format=lzma.FORMAT_XZ)
            self.objects[(pocket, path + ".xz")] = packed
            checks.extend((checksum(path, raw), checksum(path + ".xz", packed)))
        fields = [("Origin", "Ubuntu"), ("Label", "Ubuntu"),
            ("Suite", module.POCKETS[pocket][1]), ("Codename", "noble"),
            ("Architectures", "amd64 arm64"), ("Components", "main"), ("Date", date(dated)),
            ("SHA256", "\n" + "\n".join(checks))]
        if valid_until is not None:
            fields.append(("Valid-Until", date(valid_until)))
        self.set_release(pocket, stanza(fields))

    def set_release(self, pocket, text):
        self.texts[pocket] = text
        self.objects[(pocket, "InRelease")] = b"synthetic-signature-not-OpenPGP\n" + text

    def open(self, url, deadline, idle):
        self.calls.append(url)
        self.deadlines.append(deadline)
        if idle != module.IDLE_SECONDS:
            raise AssertionError("wrong synthetic idle bound")
        for pocket, (root, unused) in module.POCKETS.items():
            if url.startswith(root):
                return Response(self, url, self.objects[(pocket, url[len(root):])])
        raise AssertionError("unexpected synthetic URL")

    def verify(self, pocket, raw, keyring, signers, deadline):
        if (raw != self.objects[(pocket, "InRelease")] or keyring != self.trust["keyring_sha256"]
                or signers != self.trust["signers"]):
            raise AssertionError("synthetic verifier input join")
        self.verified.append(pocket)
        self.deadlines.append(deadline)
        return Verification(self, pocket)

    @staticmethod
    def parse(raw):
        # Standard-library stand-in only. NOT the unpinned future python-debian
        # adapter and NOT evidence that any external parser is available.
        row = Parser().parsestr(raw.decode("utf-8"), headersonly=True)
        if row.defects or row.get_payload():
            raise ValueError(SECRET)
        return {key.lower(): value for key, value in row.items()}

    @staticmethod
    def relations(expression):
        # A closed fixture table, deliberately not a home-grown APT parser.
        shared = {"name": "libsystemd-shared", "version": ("=", module.VERSION), "qualified": False}
        libc = {"name": "libc6", "version": (">=", "2.38"), "qualified": False}
        return {"": [], "libc6 (>= 2.38)": [[libc]],
                "libsystemd-shared (= " + module.VERSION + "), libc6 (>= 2.38)": [[shared], [libc]]}[expression]

    def cleanup(self, cutoff):
        self.cleanups.append(cutoff)
        for stream in self.streams:
            stream.close()
        self.after_cleanup()

    def run(self):
        return json.loads(module._evaluate_synthetic(self, clock=self.clock, utc=self.clock.utc,
            origin=10.0, baselines=self.baselines, trust=self.trust))


class CompositionTests(unittest.TestCase):
    def assert_closed(self, fixture):
        self.assertTrue(all(s.closed for s in fixture.streams))
        self.assertEqual(fixture.cleanups, [100.0])

    def rejected(self, fixture, reason=None):
        result = fixture.run()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["validation_complete"])
        if reason is not None:
            self.assertEqual(result["reason"], reason)
        self.assertTrue(result["synthetic"])
        self.assertTrue(all(result[key] is False for key in module.AUTHORITY))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assert_closed(fixture)
        return result

    def test_full_synthetic_chain_preserves_each_pocket_without_authority(self):
        fixture = Fixture()
        result = fixture.run()
        self.assertEqual(result["status"], "synthetic-complete")
        self.assertTrue(result["validation_complete"])
        self.assertTrue(result["synthetic"])
        self.assertTrue(all(result[key] is False for key in module.AUTHORITY))
        self.assertFalse(result["latest_established"])
        self.assertEqual(len(fixture.calls), 6)
        self.assertEqual(fixture.verified, list(module.POCKETS))
        self.assertEqual(set(fixture.deadlines), {70.0})
        self.assert_closed(fixture)
        self.assertLess(len(module._encode(result)), module.OUTPUT)
        self.assertNotIn(SECRET, json.dumps(result))
        for pocket in module.POCKETS:
            record = result["pockets"][pocket]
            self.assertEqual(record["inrelease_sha256"], digest(fixture.objects[(pocket, "InRelease")]))
            self.assertEqual(record["baseline_date"], fixture.baselines[pocket]["date"])
            self.assertEqual(record["records"]["binaries"]["systemd"]["dependencies"]["depends"]["unresolved_groups"], 1)

    def test_each_index_is_bound_to_its_own_release_not_union(self):
        fixture = Fixture()
        path = module.INDEXES[0] + ".xz"
        fixture.objects[("updates", path)] = lzma.compress(b"unrelated\n")
        result = self.rejected(fixture, "hash")
        self.assertEqual(set(result["pockets"]), {"security"})
        self.assertEqual(result["pockets"]["security"]["inrelease_sha256"],
                         digest(fixture.objects[("security", "InRelease")]))

    def test_unselected_empty_checksum_entry_does_not_reject_release(self):
        fixture = Fixture()
        text = fixture.texts["security"].rstrip(b"\n")
        fixture.set_release("security", text + b"\n" +
                            checksum("main/i18n/Translation-fixture", b"").encode() + b"\n\n")
        result = fixture.run()
        self.assertEqual(result["status"], "synthetic-complete")
        self.assertTrue(all(result[key] is False for key in module.AUTHORITY))
        self.assert_closed(fixture)

    def test_selected_indexes_and_source_artifacts_still_require_positive_sizes(self):
        for path in (module.INDEXES[0], module.INDEXES[0] + ".xz"):
            with self.subTest(path=path):
                fixture = Fixture()
                raw = (fixture.objects[("security", path)] if path.endswith(".xz")
                       else fixture.index_data["security"][path])
                fixture.set_release("security", fixture.texts["security"].replace(
                    checksum(path, raw).encode(), checksum(path, b"").encode()))
                self.rejected(fixture, "read-limit")
        fixture = Fixture()
        path, artifact = module.INDEXES[1], module.SOURCE_FILES[0]
        fixture.index_data["security"][path] = fixture.index_data["security"][path].replace(
            checksum(artifact, b"synthetic-source-" + artifact.encode()).encode(),
            checksum(artifact, b"").encode())
        fixture.rebuild("security")
        self.rejected(fixture, "release")

    def test_expected_pocket_not_interchangeable(self):
        fixture = Fixture()
        fixture.set_release("updates", fixture.texts["security"])
        self.rejected(fixture, "release")

    def test_conflicting_descriptors_across_authenticated_pockets(self):
        fixture = Fixture()
        path = module.INDEXES[0]
        fixture.index_data["updates"][path] = fixture.index_data["updates"][path].replace(b"Size: 100", b"Size: 101", 1)
        fixture.rebuild("updates")
        self.rejected(fixture, "record-conflict")

    def test_missing_baseline_never_passes(self):
        for value in (None, {}, {"date": NOW - 100}):
            with self.subTest(value=value):
                fixture = Fixture()
                fixture.baselines["updates"] = value
                self.rejected(fixture, "rollback-unestablished")

    def test_rollback_and_equal_date_changed_bytes(self):
        for dated in (NOW, NOW - 10):
            with self.subTest(dated=dated):
                fixture = Fixture()
                fixture.baselines["security"]["date"] = dated
                self.rejected(fixture, "rollback")

    def test_equal_baseline_exact_bytes_is_permitted_only_synthetically(self):
        fixture = Fixture()
        fixture.baselines["security"] = {"date": NOW - 10,
            "inrelease_sha256": digest(fixture.objects[("security", "InRelease")])}
        self.assertTrue(fixture.run()["validation_complete"])
        self.assert_closed(fixture)

    def test_stale_and_future_and_expired_dates(self):
        for dated, expiry in ((NOW - module.MAX_AGE - 1, None), (NOW + 61, None), (NOW - 10, NOW)):
            with self.subTest(dated=dated, expiry=expiry):
                fixture = Fixture()
                fixture.baselines["security"]["date"] = dated - 1
                fixture.rebuild("security", dated, expiry)
                self.rejected(fixture, "expired")

    def test_expiry_rechecked_after_final_cleanup(self):
        fixture = Fixture()
        fixture.rebuild("security", valid_until=NOW + 1)
        fixture.after_cleanup = lambda: setattr(fixture.clock, "wall", NOW + 1)
        self.rejected(fixture, "expired")

    def test_no_valid_until_still_has_seven_day_expiry_not_latest(self):
        fixture = Fixture()
        result = fixture.run()
        self.assertEqual(result["pockets"]["security"]["expires"], NOW - 10 + module.MAX_AGE)
        self.assertFalse(result["latest_established"])

    def test_explicit_keyring_and_separate_key_acceptability_required(self):
        for key, value in (("keyring_sha256", None), ("signers", ()), ("key_acceptable", False)):
            with self.subTest(key=key):
                fixture = Fixture()
                fixture.trust[key] = value
                self.rejected(fixture)
                self.assertEqual(fixture.verified, [])

    def test_successful_exit_and_observed_exit_both_required(self):
        for code, exited in ((1, True), (0, False), (True, True)):
            with self.subTest(code=code, exited=exited):
                fixture = Fixture()
                fixture.exit_status, fixture.exited = code, exited
                self.rejected(fixture, "signature")

    def test_status_rejects_wrong_signer_bad_or_duplicate_signature(self):
        for mutate in (lambda s: s.replace(SIGNER.encode(), b"B" * 40),
                       lambda s: s + b"[GNUPG:] BADSIG 123 " + SECRET.encode() + b"\n",
                       lambda s: s + s.splitlines(keepends=True)[-1], lambda s: b"not-status\n",
                       lambda s: s.rstrip(b"\n")):
            with self.subTest(mutate=mutate):
                fixture = Fixture()
                fixture.status = mutate(fixture.status)
                self.rejected(fixture, "signature")

    def test_signature_expiration_rechecked_at_acceptance(self):
        fixture = Fixture()
        fixture.status = fixture.status.replace(b" 0 4 0 1 8 01 ", (" %d 4 0 1 8 01 " % (NOW + 1)).encode())
        fixture.after_cleanup = lambda: setattr(fixture.clock, "wall", NOW + 1)
        self.rejected(fixture, "expired")

    def test_verifier_streams_share_diagnostic_bound(self):
        fixture = Fixture()
        fixture.diagnostics = b"x" * module.DIAGNOSTICS
        self.rejected(fixture, "read-limit")

    def test_headers_error_bodies_redirects_and_http_fail_closed(self):
        for change in ("oversize", "bad-length", "encoding", "redirect", "wrong-url", "error"):
            with self.subTest(change=change):
                fixture = Fixture()
                if change == "oversize":
                    fixture.headers = b"x" * (module.HEADERS + 1)
                elif change == "bad-length":
                    fixture.headers = b"Content-Length: 1\r\n\r\n"
                elif change == "encoding":
                    fixture.headers = b"Content-Length: 1\r\nContent-Encoding: gzip\r\n\r\n"
                elif change == "redirect":
                    fixture.redirected = True
                elif change == "wrong-url":
                    fixture.wrong_url = True
                else:
                    fixture.http_status = 500
                    fixture.objects[("security", "InRelease")] = b"x" * (module.ERROR_BODY + 1)
                self.rejected(fixture)
                self.assertEqual(len(fixture.calls), 1)

    def test_truncated_concatenated_and_trailing_compressed_data_rejected(self):
        for mutate in (lambda b: b[:-1], lambda b: b + b"trailing", lambda b: b + b):
            with self.subTest(mutate=mutate):
                fixture = Fixture()
                path = module.INDEXES[0] + ".xz"
                previous = fixture.objects[("security", path)]
                changed = mutate(previous)
                fixture.objects[("security", path)] = changed
                fixture.set_release("security", fixture.texts["security"].replace(
                    checksum(path, previous).encode(), checksum(path, changed).encode()))
                self.rejected(fixture)

    def test_compressed_hash_and_expanded_hash_are_both_required(self):
        for path in (module.INDEXES[0], module.INDEXES[0] + ".xz"):
            with self.subTest(path=path):
                fixture = Fixture()
                raw = fixture.index_data["security"][path] if not path.endswith(".xz") else fixture.objects[("security", path)]
                fixture.set_release("security", fixture.texts["security"].replace(checksum(path, raw).encode(),
                    (" " + "0" * 64 + " %d %s" % (len(raw), path)).encode()))
                self.rejected(fixture, "hash")

    def test_duplicate_fields_records_checksum_paths_and_unsafe_names(self):
        for mutate in (lambda b: b.replace(b"Package: systemd\n", b"Package: systemd\npackage: systemd\n", 1),
                       lambda b: b + binary("systemd"),
                       lambda b: b.replace(b"pool/main/s/systemd/", b"pool/../systemd/", 1)):
            with self.subTest(mutate=mutate):
                fixture = Fixture()
                fixture.index_data["security"][module.INDEXES[0]] = mutate(fixture.index_data["security"][module.INDEXES[0]])
                fixture.rebuild("security")
                self.rejected(fixture)
        fixture = Fixture()
        text = fixture.texts["security"]
        line = next(line for line in text.splitlines() if b"Packages.xz" in line)
        fixture.set_release("security", text.replace(line, line + b"\n" + line))
        self.rejected(fixture, "malformed")

    def test_missing_wrong_version_architecture_and_source_join(self):
        for old, new in ((b"Package: libsystemd-shared", b"Package: other-library"),
                         (module.VERSION.encode(), b"255.4-1ubuntu8.19"),
                         (b"Architecture: amd64", b"Architecture: arm64"),
                         (b"Source: systemd", b"Source: other-source")):
            with self.subTest(old=old):
                fixture = Fixture()
                path = module.INDEXES[0]
                fixture.index_data["security"][path] = fixture.index_data["security"][path].replace(old, new)
                fixture.rebuild("security")
                self.rejected(fixture)

    def test_mismatched_fixed_dependency_rejected_without_solver(self):
        fixture = Fixture()
        original = fixture.relations
        def relations(expression):
            groups = original(expression)
            if groups and groups[0][0]["name"] == "libsystemd-shared":
                groups[0][0]["version"] = ("=", "other-version")
            return groups
        fixture.relations = relations
        self.rejected(fixture, "dependency-mismatch")

    def test_source_members_and_source_version_are_closed(self):
        for old, new in ((b"systemd_255.4.orig.tar.gz", b"other.orig.tar.gz"),
                         (module.VERSION.encode(), b"255.4-1ubuntu8.19")):
            with self.subTest(old=old):
                fixture = Fixture()
                path = module.INDEXES[1]
                fixture.index_data["security"][path] = fixture.index_data["security"][path].replace(old, new)
                fixture.rebuild("security")
                self.rejected(fixture)

    def test_shared_acquisition_and_processing_budgets_charge_rejections(self):
        for key, maximum in (("ACQUIRED", 100), ("PROCESSED", 100)):
            with self.subTest(key=key):
                fixture = Fixture()
                with mock.patch.dict(module._read.__globals__, {key: maximum}):
                    result = self.rejected(fixture, "read-budget")
                field = "acquired" if key == "ACQUIRED" else "processed"
                self.assertEqual(result["bytes"][field], maximum)

    def test_oversize_sentinel_is_charged_before_rejection(self):
        fixture = Fixture()
        budget = module.Budget(fixture.clock, 10.0)
        stream = Stream(b"12345", fixture)
        with self.assertRaises(module.Rejected) as caught:
            module._read(stream, 4, budget, "acquired")
        self.assertEqual(caught.exception.code, "read-limit")
        self.assertEqual(budget.acquired, 5)
        self.assertEqual(budget.processed, 5)
        stream.close()

    def test_record_line_stanza_and_decoder_limits(self):
        for key, limit in (("RECORDS", 1), ("LINE", 64), ("STANZA", 64),
                           ("TOTAL_EXPANDED", 100), ("DECODER_MEMORY", 1024)):
            with self.subTest(key=key):
                fixture = Fixture()
                with mock.patch.dict(module._evaluate_synthetic.__globals__, {key: limit}):
                    self.rejected(fixture)

    def test_unfinished_last_record_line_rejected(self):
        fixture = Fixture()
        path = module.INDEXES[0]
        fixture.index_data["security"][path] = fixture.index_data["security"][path].rstrip(b"\n")
        fixture.rebuild("security")
        self.rejected(fixture, "malformed")

    def test_malformed_unselected_record_is_not_silently_skipped(self):
        fixture = Fixture()
        path = module.INDEXES[0]
        fixture.index_data["security"][path] += b"Package: unrelated\nArchitecture: amd64\n\n"
        fixture.rebuild("security")
        self.rejected(fixture, "malformed")

    def test_stall_and_close_and_verifier_finish_crossing_deadline(self):
        for hook in ("after_read", "after_close", "after_finish", "after_cleanup"):
            with self.subTest(hook=hook):
                fixture = Fixture()
                setattr(fixture, hook, lambda: setattr(fixture.clock, "monotonic", 70.0))
                self.rejected(fixture, "timeout")

    def test_clock_rollback_and_expired_original_origin(self):
        for value in (9.0, 70.0):
            with self.subTest(value=value):
                fixture = Fixture()
                fixture.clock.monotonic = value
                self.rejected(fixture, "timeout")
                self.assertEqual(fixture.calls, [])

    def test_last_freshness_read_cannot_cross_monotonic_cutoff(self):
        fixture = Fixture()
        calls = []
        def utc():
            calls.append(True)
            if len(calls) == 6:
                fixture.clock.monotonic = 70.0
            return NOW
        fixture.clock.utc = utc
        self.rejected(fixture, "timeout")
        self.assertEqual(len(calls), 6)

    def test_output_preparation_includes_deadline_and_size(self):
        fixture = Fixture()
        encode = module._encode
        def delayed(report):
            encoded = encode(report)
            fixture.clock.monotonic = 70.0
            return encoded
        with mock.patch.dict(module._evaluate_synthetic.__globals__, {"_encode": delayed}):
            self.rejected(fixture, "timeout")
        with self.assertRaises(module.Rejected) as caught:
            encode({"oversize": "x" * module.OUTPUT})
        self.assertEqual(caught.exception.code, "output-limit")

    def test_cleanup_exception_never_retries_or_claims_absence(self):
        fixture = Fixture()
        def fail():
            raise OSError(SECRET)
        fixture.after_cleanup = fail
        self.rejected(fixture)

    def test_exception_diagnostics_never_escape(self):
        fixture = Fixture()
        def fail(*args):
            raise OSError(SECRET)
        fixture.verify = fail
        self.rejected(fixture, "internal")

    def test_parser_return_contract_and_dependency_expansion_are_bounded(self):
        for kind in ("parser", "relations"):
            with self.subTest(kind=kind):
                fixture = Fixture()
                if kind == "parser":
                    fixture.parse = lambda raw: {SECRET: SECRET}
                else:
                    fixture.relations = lambda expression: [[]] * 129
                self.rejected(fixture, "boundary")


class EntrypointTests(unittest.TestCase):
    def invoke(self, argv, *, write_error=False, short=False, flush_error=False):
        output = mock.Mock()
        attempted = []
        def write(text):
            attempted.append(text)
            if write_error:
                raise OSError(SECRET)
            return len(text) - int(short)
        output.write.side_effect = write
        if flush_error:
            output.flush.side_effect = OSError(SECRET)
        with mock.patch.object(module.sys, "argv", argv), mock.patch.object(module.sys, "stdout", output), \
                mock.patch.dict(module.main.__globals__, {"_evaluate_synthetic": mock.Mock(side_effect=AssertionError("live core called"))}):
            self.assertEqual(module.main(), 2)
        result = json.loads(attempted[0])
        self.assertTrue(all(result[key] is False for key in module.AUTHORITY))
        self.assertFalse(result["synthetic"])
        self.assertNotIn(SECRET, attempted[0])
        return result

    def test_default_and_arbitrary_invocation_refuse_without_acquisition(self):
        self.assertEqual(self.invoke(["collector"])["reason"], "live-disabled")
        self.assertEqual(self.invoke(["collector", "--approved"])["reason"], "invocation")

    def test_changing_precondition_values_cannot_enable_execution(self):
        self.assertTrue(all(value is None for value in module.LIVE_PRECONDITIONS.values()))
        with mock.patch.dict(module.LIVE_PRECONDITIONS, dict.fromkeys(module.LIVE_PRECONDITIONS, True)):
            self.assertEqual(self.invoke(["collector"])["reason"], "live-disabled")

    def test_output_failures_remain_nonzero(self):
        for kwargs in ({"write_error": True}, {"short": True}, {"flush_error": True}):
            with self.subTest(kwargs=kwargs):
                self.invoke(["collector"], **kwargs)

    def test_no_process_network_filesystem_environment_or_dynamic_loader(self):
        tree = ast.parse(SOURCE.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"eval", "exec", "compile", "__import__", "open"})
        self.assertEqual(imports, {"email.utils", "hashlib", "json", "lzma", "math", "re", "sys"})

    def test_existing_resource_helper_unchanged(self):
        helper = ROOT / "host/probe-build-resources.py"
        self.assertEqual(digest(helper.read_bytes()),
            "2b740ef5b1ecbc87cb3a078fcbb081017f7db5c9600c9956f97cc52330ad9fdc")


if __name__ == "__main__":
    unittest.main()

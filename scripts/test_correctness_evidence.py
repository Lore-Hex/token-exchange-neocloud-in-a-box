"""Preserve original failed hardware and local diagnostic recorder output."""
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


class CorrectnessEvidenceTests(unittest.TestCase):
    def test_vendored_attempt_stopped_before_entry_with_historical_payload_binding(self):
        import test_engine_cpu_entry as cpu_tests

        tool = cpu_tests.tool
        root = Path(__file__).resolve().parents[1]
        directory = root / "evidence/p2/2026-10-20-cpu-vendored-7fb794a"
        revision = "7fb794a442287bb6c7ac974c2a72f1067ed22fe6"
        documents = []
        for name, digest in (
            ("run.json", "3b013bb7e0597537cfaec42d20bba1683794ccac4b295a2538f949483e575f28"),
            ("output.log", "3e25936d68048e5e5c92ac1772a8674838a0ebc9bee3728d2fc72e8bd9b9c092"),
        ):
            raw = (directory / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual((run["git_sha"], run["execution"], run["result"]), (revision, "software", "fail"))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-20T16:12:39Z", "2026-10-20T16:25:06Z"))
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-cpu-entry.py --slug cpu-vendored-7fb794a")
        self.assertEqual(run["host"], {"provider": "local", "machine": "local-software-experiment",
                                      "gpus": "none", "driver": None, "cuda": None})
        self.assertFalse(run["hardware_validated"])
        self.assertEqual((report["stage"], report["cleanup"], report["result"]), ("completed", "confirmed", "fail"))
        experiment = report["experiment"]
        self.assertEqual(experiment["stage"], "application")
        self.assertEqual(experiment["failure"], "bounded-cpu-entry-rejected")
        self.assertEqual(experiment["phase_elapsed_ms"],
                         {"application-preflight": 18233, "application": 701769, "entry": None})
        self.assertIsNone(experiment["application"])
        self.assertIsNone(experiment["entry"])
        for row in (report, experiment):
            for key in ("entry_pass", "confidential", "deployable", "inference_started", "hardware_validated", "abi_validated"):
                self.assertIs(row[key], False)
            self.assertEqual(row["execution"], "software")
        self.assertEqual(report["candidate_status"], "BLOCKED")
        self.assertEqual(experiment["status"], "BLOCKED")

        # Execute only pinned repository preparation helpers, never the payload,
        # Docker or upstream sources. Preserve the historical helper closure.
        names = (
            "scripts/record-engine-cpu-entry.py", "scripts/engine_cpu_entry.py", "scripts/engine_lab.py",
            "scripts/engine_lab_bootstrap.py", "scripts/record-engine-import-smoke.py",
            "scripts/prepare-engine-application.py", "scripts/engine_application.py",
            "scripts/engine_python_bootstrap.py", "scripts/engine_import_smoke.py",
            "scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py",
            "scripts/engine_overlay.py", "scripts/check-engine-candidate.py",
            "scripts/e2e/snapshot.py", "scripts/engine_image_diagnostic.py",
            "scripts/engine_python_descriptor.py",
        )
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary).resolve()
            for name in names:
                raw = tool.checker.git(root, "show", revision + ":" + name)
                self.assertLessEqual(len(raw), 2 << 20)
                path = source_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            source = (source_root / names[0]).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(),
                             "f007c3b3470ae92d09d245f6efb1a65b3cfe895abd3ce8d5ac8c5a3f87d2a9f1")
            historical = types.ModuleType("historical_cpu_entry")
            historical.__file__ = str(source_root / names[0])
            exec(compile(source, historical.__file__, "exec"), historical.__dict__)
            original_git = historical.checker.git

            def source_git(repo, *args, **kwargs):
                if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                    return (revision + "\n").encode()
                return original_git(repo, *args, **kwargs)

            with mock.patch.object(historical.checker, "git", side_effect=source_git):
                files, contract = historical.prepare_files(*historical.inputs(root))
            overlay = historical.overlay
            payload = overlay.bundle_digest(files)
            self.assertEqual(payload, "d0246f9465eb864072203397ed2b495ae15c4694fbbf4b79c4673b2648d9b4a8")
            provenance = report["provenance"]
            self.assertEqual(provenance["payload_sha256"], payload)
            self.assertEqual(provenance["git_sha"], revision)
            self.assertEqual(provenance["source_bundle_sha256"], contract["source_bundle_sha256"])
            self.assertEqual(provenance["measurement_sha256"], contract["measurement_sha256"])
            self.assertEqual(provenance["expected_config_sha256"], contract["official_config"]["sha256"])
            self.assertEqual(provenance["requested_image"], overlay.BASE_IMAGE)
            self.assertTrue(provenance["raw_oci_metadata_verified"])
            self.assertIn(payload, run["notes"])
            self.assertTrue(report["image"]["raw_config_link_verified"])
            self.assertFalse(report["image"]["local_config_bytes_rehashed"])
            self.assertFalse(report["image"]["local_layers_rehashed"])
            self.assertEqual(report["image"]["manifest_sha256"], overlay.BASE_IMAGE.split("@sha256:")[1])
            self.assertEqual(report["image"]["raw_config_sha256"], contract["official_config"]["sha256"])
            self.assertEqual(report["image"]["platform"], "linux/amd64")
            # Match the producer's validator-source selection without building
            # or invoking a second validator in this artifact regression.
            entries = original_git(root, "ls-tree", "-r", "-z", revision, "--",
                                   "go.mod", "go.sum", "cmd/nib", "internal", "schemas")
            validator = {}
            for entry in entries.split(b"\0"):
                if not entry:
                    continue
                meta, name = entry.decode().split("\t")
                mode, kind, oid = meta.split()
                self.assertIn(mode, ("100644", "100755"))
                self.assertEqual(kind, "blob")
                self.assertLessEqual(int(original_git(root, "cat-file", "-s", oid)), historical.checker.LIMIT)
                validator[name] = original_git(root, "cat-file", "blob", oid)
            self.assertEqual(overlay.bundle_digest(validator), provenance["validator_source_sha256"])
            application = overlay.load_json(files["application/contract.json"])
            measured = historical.builder.application.measurement(overlay, files["application/measurement.json"])
            self.assertEqual(historical.cpu.APPLICATION_SECONDS, 720)
            self.assertEqual(sum(value for value in experiment["phase_elapsed_ms"].values() if value is not None),
                             historical.cpu.APPLICATION_SECONDS * 1000 + 2)
            self.assertFalse(historical.cpu.validate(overlay, experiment, application, measured, historical.base.smoke))
            with self.assertRaises(ValueError):
                historical.cpu.validate(overlay, {**experiment, "entry_pass": True},
                                        application, measured, historical.base.smoke)

    def test_cpu_entry_failure_records_only_the_observed_import_boundary(self):
        for name, source, run_digest, output_digest in (
            ("cpu-entry-125036c", "125036cb61a9d15d624f5d1c8a0c65d69076574c",
             "c5a60637849450294deea13af77ddffea9fe8bdfe850a1b4d4f2466885d87b15",
             "f4985fd5f781e0324cce4d4277970ad1a5e67c24aaa8d1395fe945c7f197d6c3"),
            ("cpu-entry-732fe9f", "732fe9fd77e5e2b877fdfa04ac18547d4e437f25",
             "a45e523ef0bef472c6ade2878019aa8a6ce237f84192d51c0b7547cfa5edf6fa",
             "d0bc25522652f61e63e258bef3c47688400d88cbc8cf68948c372043b7874035"),
            ("cpu-finder-9833667", "983366709ffb1bd0682fb7692bb71b1e4777a450",
             "36112d70c3a91c0f45b20da58dcffc6e916859771e72bafceeeb1b1e134f001b",
             "c9ec7cb8d8eb841d3dcea5d0c7fd1fd88b7c10df749966b0ce46a836824c50e0"),
        ):
            with self.subTest(source=source):
                self.check_cpu_entry_failure(name, source, run_digest, output_digest)

    def check_cpu_entry_failure(self, name, source, run_digest, output_digest):
        root = Path(__file__).resolve().parents[1] / ("evidence/p2/2026-10-20-" + name)
        values = {}
        for name, digest in (("run.json", run_digest), ("output.log", output_digest)):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], source)
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertEqual(run["host"]["gpus"], "none")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], "fail")
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertEqual(output["stage"], "completed")
        experiment = output["experiment"]
        self.assertEqual(experiment["application"]["stages"], 5)
        self.assertEqual(experiment["entry"]["checkpoints"], {"status": "eof", "last_id": 11})
        self.assertEqual(experiment["entry"]["exit_code"], 1)
        self.assertEqual(experiment["entry"]["state_observation"], "observed")
        self.assertEqual({key for key, value in experiment["entry"]["state_mismatches"].items() if value},
                         {"finders"})
        self.assertEqual(len(experiment["entry"]["state_mismatches"]), 10)
        for row in (output, experiment):
            for key in ("entry_pass", "confidential", "deployable", "inference_started", "hardware_validated"):
                self.assertFalse(row[key])

    def test_unknown_finder_remains_unidentified_and_blocked(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-cpu-finder-9833667"
        output = json.loads((root / "output.log").read_bytes())
        self.assertEqual(output["experiment"]["entry"]["finder_diagnostic"], {
            "status": "observed",
            "entries": ["builtin", "frozen", "sealed", "unknown"],
            "comparison_failed": False,
            "expected_duplicated": False,
            "expected_order_changed": False,
            "missing_expected_in_scan": [],
            "pattern_adapter_stage": "pending",
            "same_identity_list": False,
            "scan_truncated": False,
            "sources": [
                {"id": name, "status": "verified"}
                for name in ("vllm-env", "setuptools-hack", "metadata-backport", "six")
            ],
        })
        self.assertEqual(output["candidate_status"], "BLOCKED")
        self.assertEqual(output["result"], "fail")

    def test_original_failure_is_preserved_without_inferred_runtime_success(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-correctness-recorder-failed"
        values = {}
        for name, digest in (
            ("run.json", "6d1a3383e54e9303927e72880e276282a96d93f130a0978a2af2f4eaa575ea88"),
            ("output.log", "b8275e6549395a83ad529728fe5920b9334c480d55eeb41f4654a37ea10428dc"),
        ):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["git_sha"], "26a7240b53ee0590d98655b12c6b57ca4779c2aa")
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], "fail")
        self.assertEqual(output["stage"], "experiment")
        self.assertEqual(output["failure"], "bounded-lab-stage-failed")
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertNotIn("experiment", output)
        for key in ("functional_pass", "confidential", "deployable"):
            self.assertFalse(output[key])

    def test_validated_startup_failure_is_not_inference_or_oom(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-correctness-startup-failed"
        values = {}
        for name, digest in (
            ("run.json", "b2d7749f0a5e3b400cd6525f202b95186f1b3b20bfd989df13b2a7ed717da8ae"),
            ("output.log", "c229a6a25a0463b53d2bfe3eecae55235fe8b7af49852f682f01224fff9bfe26"),
        ):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], "bac13b0fac39096a0990417f3cfcf4e623e911ac")
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["execution"], "hardware")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertEqual(output["experiment_step"], "completed")
        self.assertEqual(output["container_observation"],
                         {"status": "exited", "exit_code": 0, "oom_killed": False})
        experiment = output["experiment"]
        self.assertEqual(experiment["stage"], "startup")
        self.assertEqual(experiment["application"]["stages"], 5)
        self.assertTrue(experiment["hardware"]["system_ready"])
        self.assertEqual(experiment["startup"],
                         {"outcome": "child-exited", "exit_code": 1,
                          "wait_ms": 8003, "health_probes": 8})
        self.assertIsNone(experiment["completion"])
        self.assertIsNone(experiment["correctness"])
        for row in (output, experiment):
            for key in ("functional_pass", "confidential", "deployable"):
                self.assertFalse(row[key])


if __name__ == "__main__":
    unittest.main()

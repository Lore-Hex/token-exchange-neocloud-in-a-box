# SPDX-License-Identifier: Apache-2.0
"""Thin opt-in sender for a pinned POSIX command transport; no SSH defaults."""

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import time
import types
import zlib

ROOT = Path(__file__).absolute().parents[1]
ENTRY = "host/receive-original-image-inputs.py"
SELF = "scripts/original_image_transfer.py"
VERIFIER = "host/verify-selected-image-archive.py"
VERIFIER_SHA = "e24de243aa0ddc0f7c5bc61aab85152bf9ec1eeccd96a9e35ce4b299c0544968"
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}


def require(value):
    if not value:
        raise ValueError("original-image-transfer-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(raw, path):
    module = types.ModuleType("original_transfer_local_" + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def authenticated(raw, expected):
    """expected must come from independent caller approval, never a sidecar field."""
    require(type(raw) is bytes and 0 < len(raw) <= 65536 and sha(raw) == expected)
    with (ROOT / VERIFIER).open("rb") as stream:
        helper_raw = stream.read((1 << 20) + 1)
    require(sha(helper_raw) == VERIFIER_SHA)
    files = load(helper_raw, ROOT / VERIFIER)
    value = files.strict_json(raw)
    require(files.encoded(value) == raw)
    # Authenticate all code, including this sender, before importing the entrypoint.
    require(type(value) is dict and type(value.get("code")) is dict
            and set(value["code"]) == {SELF, ENTRY, VERIFIER, "host/prepare-lab.py",
                                     "scripts/e2e/snapshot.py", "scripts/original_image_input.py"})
    sources = {name: files.read(ROOT / name, 1 << 20) for name in value["code"]}
    require(all(sha(source) == value["code"][name] for name, source in sources.items()))
    adapter = load(sources[ENTRY], ROOT / ENTRY)
    value = adapter.manifest(raw, expected, files)
    runner = load(sources[adapter.RUNNER], ROOT / adapter.RUNNER)
    return value, sources, adapter, files, runner


def transport(raw, expected, files):
    require(type(raw) is bytes and 0 < len(raw) <= files.MAX_REPORT and sha(raw) == expected)
    value = files.strict_json(raw)
    require(files.encoded(value) == raw and type(value) is dict and set(value) == {
        "schema", "argv", "executable_sha256", "remote_python"}
        and value["schema"] == "nib.pinned-posix-command-transport/v1")
    argv = value["argv"]
    require(type(argv) is list and 2 <= len(argv) <= 128
            and all(type(part) is str and part and "\0" not in part for part in argv)
            and argv[-1] == "{command}" and "{command}" not in argv[:-1])
    for path in (argv[0], value["remote_python"]):
        require(type(path) is str and path.startswith("/") and "\0" not in path
                and os.path.normpath(path) == path and ".." not in Path(path).parts)
    require(type(value["executable_sha256"]) is str and len(value["executable_sha256"]) == 64)
    return value


def bootstrap_program(packet, sources, code_pins):
    """Code travels in the authenticated command, never in the payload pipe.

    The command and its pins are rooted in the caller-authenticated local
    manifest and transport. A remote file or receipt cannot select code to run.
    """
    bundle = json.dumps({"packet": packet, "sources": {name: base64.b64encode(raw).decode()
                        for name, raw in sources.items()}}, separators=(",", ":")).encode()
    require(len(bundle) <= 1 << 20 and set(sources) == set(code_pins)
            and all(sha(raw) == code_pins[name] for name, raw in sources.items()))
    compressed = base64.b64encode(zlib.compress(bundle)).decode()
    body = f"""d=zlib.decompressobj()
r=d.decompress(base64.b64decode({compressed!r},validate=True),(1<<20)+1)
if len(r)>(1<<20) or not d.eof or d.unused_data or d.unconsumed_tail or hashlib.sha256(r).hexdigest()!={sha(bundle)!r}: raise ValueError('bootstrap-rejected')
b=json.loads(r); p=b['packet']; pins={code_pins!r}
s={{n:base64.b64decode(v,validate=True) for n,v in b['sources'].items()}}
if set(s)!=set(pins) or any(hashlib.sha256(v).hexdigest()!=pins[n] for n,v in s.items()): raise ValueError('bootstrap-rejected')
m=types.ModuleType('authenticated_original_input_entry')
m.__file__=p['root']+'/{ENTRY}'
exec(compile(s[{ENTRY!r}],m.__file__,'exec',dont_inherit=True),m.__dict__)
if p['action']=='bootstrap':
 result=m.initialize(p,s)
else:
 result=m.phase(m.Path(p['root']),p['manifest_sha256'],p['action'],p['previous'],0,p['clock'],p['deadline'],p['intent_sha256'],s[{VERIFIER!r}])
f=m.load(s[{VERIFIER!r}],p['root']+'/{VERIFIER}')
m.emit(result,f)
"""
    program = ("import base64,hashlib,json,os,types,zlib\ntry:\n"
               + "".join(" " + line + "\n" for line in body.splitlines())
               + "except BaseException:\n os._exit(1)\nos._exit(0)\n")
    require(len(program.encode()) <= 65536)
    return program


def clock_program(nonce):
    return ("import json,os,pathlib,time; "
        "s=os.stat('/proc/self/ns/time'); "
        "print(json.dumps({'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),"
        "'clock_namespace':[s.st_dev,s.st_ino],'monotonic':time.monotonic(),"
        f"'nonce':{nonce!r}}},sort_keys=True,separators=(',',':')))" )


def invoke(channel, program, stream, deadline, adapter, files, runner):
    command = shlex.join([channel["remote_python"], "-I", "-S", "-B", "-c", program])
    require(len(command.encode()) <= files.MAX_REPORT)
    binary = Path(channel["argv"][0])
    with files.regular(binary) as (executable, info):
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022
                and info.st_mode & 0o111 and 0 < info.st_size <= 16 << 20)
        raw = executable.read((16 << 20) + 1)
        require(len(raw) == info.st_size and sha(raw) == channel["executable_sha256"])
        left = adapter.check(deadline)
        result = runner.execute_process_group(channel["argv"][:-1] + [command], env=ENV,
            timeout=left, stdin=stream, max_output_bytes=files.MAX_REPORT, max_stderr_bytes=4096)
    adapter.check(deadline)
    require(result.returncode == 0 and result.stderr == b"")
    raw = result.stdout
    parsed = files.strict_json(raw)
    require(type(parsed) is dict and files.encoded(parsed) == raw)
    return parsed


def validate_ack(result, value, manifest_sha, phase, previous, binding, deadline, adapter, intent_sha=None):
    adapter.hex_value(result.get("intent_sha256"), 64)
    require(intent_sha is None or result["intent_sha256"] == intent_sha)
    expected = {"schema": adapter.SCHEMA, "kind": "terminal-ack", "phase": phase,
        "manifest_sha256": manifest_sha, "attempt_id": value["attempt_id"],
        "producer": value["producer"], "image_producer": value["image_producer"],
        "previous_ack_sha256": previous, "deadline_monotonic": deadline,
        "clock": binding, "intent_sha256": result["intent_sha256"], **{name: False for name in adapter.FLAGS}}
    require(all(result.get(key) == val and type(result.get(key)) is type(val)
                for key, val in expected.items()))
    if phase in adapter.ROLES:
        require(set(result) == set(expected) | {"component", "request_sha256",
            "component_receipt_sha256", "verified_bytes", "sha256"}
            and result["component"] == phase and type(result["verified_bytes"]) is int
            and result["verified_bytes"] == value["components"][phase]["bytes"]
            and result["sha256"] == value["components"][phase]["sha256"])
        adapter.hex_value(result["component_receipt_sha256"], 64)
    elif phase == "final":
        require(set(result) == set(expected) | {"source", "image_id", "archive", "inputs_joined"}
                and result["inputs_joined"] is True and result["archive"] == value["components"]["image"])
    else:
        require(set(result) == set(expected))


def validate_final(result, value, inputs, files):
    reports = {}
    for name, limit in (("verification", 65536), ("build", 16384)):
        raw = files.read(Path(inputs[name]), limit, private=True)
        require(sha(raw) == value["components"][name]["sha256"])
        reports[name] = files.strict_json(raw)
    require(result["source"] == reports["verification"]["source"]
            and result["source"]["build_producer"] == value["image_producer"]
            and result["image_id"] == reports["build"]["image_id"])


def _send(raw_manifest, manifest_sha, raw_transport, transport_sha, inputs, output, *, deadline):
    """Caller supplies independently authenticated pins and an approved transport.

    No CLI/default target, credential discovery, packaging, import, or retry.
    Filesystem calls can stall. Local process cleanup is not remote absence.
    """
    entered = time.monotonic()
    value, sources, adapter, files, runner = authenticated(raw_manifest, manifest_sha)
    require(type(deadline) in (int, float) and math.isfinite(deadline)
            and deadline <= entered + value["seconds"])
    adapter.check(deadline)
    channel = transport(raw_transport, transport_sha, files)
    require(type(inputs) is dict and set(inputs) == set(adapter.ROLES))
    output = Path(output)
    require(output.name == value["attempt_id"])
    parent = files.directory(output.parent, private=True)
    try:
        adapter.check(deadline)
        os.mkdir(output.name, 0o700, dir_fd=parent)
        fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fsync(parent)
            adapter.anchored(output, parent, fd, files, deadline)
            files.publish(fd, "intent.json", {"manifest_sha256": manifest_sha,
                "transport_sha256": transport_sha, "deadline_monotonic": deadline,
                "attempt_id": value["attempt_id"], "uncertain_until_terminal_exit": True})
            adapter.write_file(fd, "manifest.json", raw_manifest, deadline)
            adapter.anchored(output, parent, fd, files, deadline)
            # Account conservatively for clock-probe round trip, never compare
            # sender/receiver monotonic epochs or reset time for a component.
            with open(os.devnull, "rb") as stream:
                observed = invoke(channel, clock_program(manifest_sha), stream,
                                  deadline, adapter, files, runner)
            require(set(observed) == {"boot_id", "clock_namespace", "monotonic", "nonce"}
                    and observed["nonce"] == manifest_sha
                    and type(observed["monotonic"]) in (int, float)
                    and math.isfinite(observed["monotonic"]))
            binding = {key: observed[key] for key in ("boot_id", "clock_namespace", "monotonic")}
            remote_deadline = observed["monotonic"] + adapter.check(deadline)
            packet = {"root": value["destination"], "manifest": raw_manifest.hex(),
                "manifest_sha256": manifest_sha, "clock": binding, "deadline": remote_deadline}
            previous, intent_sha = None, None
            for name in ("bootstrap", *adapter.ROLES, "final"):
                adapter.anchored(output, parent, fd, files, deadline)
                files.publish(fd, name + ".intent.json", {"previous_ack_sha256": previous,
                                                         "manifest_sha256": manifest_sha})
                program = bootstrap_program({**packet, "action": name, "previous": previous,
                                             "intent_sha256": intent_sha},
                                            sources, value["code"])
                if name in adapter.ROLES:
                    with files.regular(Path(inputs[name]), private=True) as (stream, info):
                        require(info.st_size == value["components"][name]["bytes"])
                        _, receiver, _ = adapter.helpers()
                        receiver.rehash(Path(inputs[name]), value["components"][name],
                                        files.identity(info), files, deadline)
                        result = invoke(channel, program, stream, deadline, adapter, files, runner)
                else:
                    with open(os.devnull, "rb") as stream:
                        result = invoke(channel, program, stream, deadline, adapter, files, runner)
                validate_ack(result, value, manifest_sha, name, previous, binding, remote_deadline, adapter, intent_sha)
                intent_sha = result["intent_sha256"]
                if name in adapter.ROLES:
                    require(result["request_sha256"] == sha(adapter.component_request(value, name, files)))
                if name == "final":
                    validate_final(result, value, inputs, files)
                adapter.anchored(output, parent, fd, files, deadline)
                files.publish(fd, name + ".exit-zero-ack.json", result)
                previous = sha(files.encoded(result))
            adapter.anchored(output, parent, fd, files, deadline)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    adapter.check(deadline)
    return result


def send(raw_manifest, manifest_sha, raw_transport, transport_sha, inputs, output, *, deadline):
    """One call only; even a failed call consumes its fresh local attempt path."""
    try:
        return _send(raw_manifest, manifest_sha, raw_transport, transport_sha, inputs, output,
                     deadline=deadline)
    except Exception:
        raise ValueError("original-image-transfer-rejected") from None

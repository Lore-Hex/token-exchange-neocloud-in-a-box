# SPDX-License-Identifier: Apache-2.0
"""Offline synthetic workload count in the already prepared selected runtime.

Invoked only as a bounded child by selected_long_qualification.run_owned. This
does not start an engine, open a socket, download files or load model weights.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Isolated tokenizer preparation required.")
    sys.path.insert(0, str(Path(__file__).absolute().parent))

import os
import signal
import time
import types
from contextlib import contextmanager

import selected_long_qualification as qualification

process, service = qualification.process, qualification.service
require = qualification.require


@contextmanager
def tokenizer_runtime(expected, deadline, *, execution_profile=None):
    require(str(Path(__file__).absolute()) == str(process.ROOT / qualification.CHILD)
            and sys.version_info[:3] == (3, 12, 3), "contract")
    raw = process.bootstrap.read_source(str(process.ROOT / "prepared.json"))
    require(process.digest(raw) == expected, "identity")
    receipt = service.correctness.strict_json(raw)
    require(receipt == process.preparation_receipt(process.ROOT, receipt["lab_sha256"]), "identity")
    for path in (process.ROOT, process.PAYLOAD, Path("/model")):
        require(os.statvfs(path).f_flag & os.ST_RDONLY, "identity")
    source = process.bootstrap.read_source(str(process.PAYLOAD / "launch.py"))
    require(source == process.launcher_source(process.ROOT, selected=True), "identity")
    lab = types.ModuleType("long_qualification_lab")
    lab.__file__ = str(process.PAYLOAD / "launch.py")
    exec(compile(source, lab.__file__, "exec"), lab.__dict__)
    contract, _, modules, application, _ = lab.lab_context(receipt["lab_sha256"])
    process.engine_entry.canonical_arguments(contract)
    require(contract["execution_profile"] in process.engine_entry.SELECTED_LONG_CONTEXT_PROFILES
            and (execution_profile is None and contract["execution_profile"] in process.engine_entry.LONG_PROFILES
                 or execution_profile == contract["execution_profile"] == process.engine_entry.MILLION_PROFILE)
            and contract["model_profile"] == service.models.select("uncensored"), "identity")
    lab.recheck_sources(modules[0], application, contract["paths"])
    lab.PARITY.verify_sources(lab.read_source, contract["paths"][-1], lab.CORRECTNESS)
    lab.PARITY.verify_model(lab.read_source)
    state = lab.install_lab(lab.lab_layout(contract), lab)
    try:
        # Same loader, verified source tree and local tokenizer bytes as the
        # existing lab parity child. No guessed Python package/version fallback.
        from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer
        tokenizer = DeepseekV41Tokenizer.from_pretrained("/model", local_files_only=True, trust_remote_code=False)
        yield tokenizer, contract
        state["check"]()
        lab.PARITY.verify_model(lab.read_source)
        lab.PARITY.verify_sources(lab.read_source, contract["paths"][-1], lab.CORRECTNESS)
        lab.recheck_sources(modules[0], application, contract["paths"])
    finally:
        state["check"]()


def prepare_runtime(expected, deadline, *, execution_profile=None):
    with tokenizer_runtime(expected, deadline, execution_profile=execution_profile) as (tokenizer, contract):
        workload = qualification.prepare(tokenizer, deadline=deadline,
                                         execution_profile=contract["execution_profile"])
    return {"prepared_sha256": expected, "workload": workload}


def main():
    report_fd = os.dup(1)
    os.set_inheritable(report_fd, False)
    try:
        with open(os.devnull, "r+b", buffering=0) as sink:
            for number in (0, 1, 2):
                os.dup2(sink.fileno(), number)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        require(len(sys.argv) in (2, 3), "contract")
        execution = sys.argv[2] if len(sys.argv) == 3 else None
        require(execution is None or execution == process.engine_entry.MILLION_PROFILE, "contract")
        seconds = qualification.workload_profile(execution or process.engine_entry.LONG_PROFILE).prepare_seconds
        signal.alarm(seconds)
        result = prepare_runtime(sys.argv[1], time.monotonic() + seconds, execution_profile=execution)
        raw = qualification.encoded(result)
        require(len(raw) <= 8192 and os.write(report_fd, raw) == len(raw), "protocol")
        return 0
    except BaseException:
        return 1
    finally:
        os.close(report_fd)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read an intact interrupted v3 calibration prefix from stdin, without recovery."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/audit-quality-interruption.py")

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tau_trial as trial


def main():
    trial.require(len(sys.argv) == 1)
    summarizer = trial.load("scripts/summarize-quality-calibration.py")
    raw = sys.stdin.buffer.read((1 << 20) + 1)
    sys.stdout.buffer.write(trial.worker.encoded(summarizer.audit_interrupted(raw)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Interrupted calibration audit rejected; preserve the original receipt unchanged.")

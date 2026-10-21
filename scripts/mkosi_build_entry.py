#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Load only the authenticated, read-only mkosi tree in the isolated runtime."""
import os
import runpy
import sys


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and sys.version_info[:2] == (3, 12) and os.geteuid() == 0):
        raise ValueError("mkosi-entry-rejected")
    sys.path.insert(0, "/nib/upstream")
    runpy.run_module("mkosi", run_name="__main__")


if __name__ == "__main__":
    main()

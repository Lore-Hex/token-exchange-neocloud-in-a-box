# SPDX-License-Identifier: Apache-2.0
"""Exercise the new collector with the existing private-pipe failure fixture."""

from dataclasses import replace
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stream_scaling as scaling


def child(model):
    collect = scaling.stream.OwnedStreamHTTP.collect
    def checked(self, *args, **kwargs):
        scaling.binding.sha(kwargs["cache_salt"])
        result = collect(self, *args, **kwargs)
        # Only synthetic non-HTTP fixtures supply synthetic cache usage.
        return result if sys.argv[1].startswith("http") else replace(result, cached_prompt_tokens=0)
    scaling.stream.OwnedStreamHTTP.collect = checked
    scaling.child(model)


scaling.c8.child = child
runpy.run_path(str(Path(__file__).with_name("stream_c8_child.py")), run_name="__main__")

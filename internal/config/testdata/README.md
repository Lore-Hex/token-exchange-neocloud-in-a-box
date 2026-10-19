<!-- SPDX-License-Identifier: Apache-2.0 -->

# Synthetic Schema Fixtures

All files here are Apache-2.0 test inputs, **not production evidence**. Digests,
revisions, hardware facts, timestamps and command lines are synthetic. Even the
hardware-claim fixture demonstrates only a permitted JSON shape, not a run.

`valid/` covers complete/draft packs, each hardware CC mode, release manifests,
and fake/software/hardware/not-run/failed evidence. `invalid/<kind>/` contains
independent documents named for the violated rule: every required field and
typed boundary, every closed object, pins, formats, bounds, CC-mode relationships,
and evidence consistency. The deployable overlay has separate incomplete-pin
and DeepSeek UVA fixtures. Tests also validate YAML serialization of valid packs
and profiles.

Parser ambiguity, exact decimal/exponent arithmetic, file-manifest rules and
cross-file repository constraints use named table-driven fixtures in the test
sources because they are not standalone schema documents. Repository fixtures
are materialized only in `testing.T.TempDir`, never under production evidence.

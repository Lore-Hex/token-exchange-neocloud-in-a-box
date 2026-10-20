# P2.T3 Fixed Finder Identity Diagnostic

This adds observations to the existing [CPU entry diagnostic](p2-t3-cpu-entry.md).
It does not adapt another hook, authorize finder changes, change the lab guard,
alter the pattern adapter, or relax P0 admission. It executes no image as part
of implementation/testing and claims no identified cause of a prior run.
The candidate stays BLOCKED, non-deployable and non-confidential.

## Public Catalog

Before engine import, the existing bounded `compiled_catalog` helper reads
four exact selected-root files, checks full byte length and SHA-256, and
compiles them with `dont_inherit=True`, `optimize=0`. It never executes these
reference code objects or imports the referenced dependencies. Each method
selector must resolve uniquely. Missing, unreadable, changed, uncompilable or
unselectable sources produce fixed statuses, not an exception message.

| Source | Bytes | SHA-256 |
| --- | --- | --- |
| `vllm/env_override.py` | 37970 | `8a22650f862565a9ce2ba4441d39a99afbcecc2b60664be2272dd18e2df1cd1d` |
| `_distutils_hack/__init__.py` | 6755 | `df81e6bcba34ee3e3952f776551fb669143b9490fdd6c4caeb32609f97e985b4` |
| `importlib_metadata/__init__.py` | 37734 | `bbb130e3e524a73358fa46ba93e591903850652d5a9242cc7d6b367849d4986a` |
| `six.py` | 34703 | `c51c91f703d3d4b3696c923cb5fec213e05e75d9215393befac7f2fa6a3904df` |

Fixed IDs and immutable primary sources:

- `vllm-cpp`: [`_patch_cpp_indirect_assert_if_needed.<locals>._CppCodegenPatchFinder.find_spec`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/env_override.py#L800).
  Its upstream version gate is Torch 2.11 through before 2.12, not the selected
  Torch 2.13. Catalog inclusion is not a claim that the gate executes.
- `vllm-pattern`: [`_patch_inductor_pattern_matcher.<locals>._PatternMatcherPatchFinder.find_spec`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/env_override.py#L947).
  The existing exact-source adapter remains unchanged.
- `setuptools-distutils`: [`DistutilsMetaFinder.find_spec`](https://github.com/pypa/setuptools/blob/5cf2d085186f2c8053940076db99045b826ec22a/_distutils_hack/__init__.py#L101).
- `metadata-backport`: [`MetadataPathFinder.find_distributions`](https://github.com/python/importlib_metadata/blob/84e9028d39062af975d0659c0e987c28bcc808a5/importlib_metadata/__init__.py#L970).
- `six`: [`_SixMetaPathImporter.find_spec`](https://github.com/benjaminp/six/blob/ebd9b3af90247b8858d415a05e96e9ee61e48d07/six.py#L194),
  six 1.17.0. Its [registration](https://github.com/benjaminp/six/blob/ebd9b3af90247b8858d415a05e96e9ee61e48d07/six.py)
  appends a finder. This is a source-backed possible mutation, not observed
  causality in an image.

The inspected pinned Torch
[`__init__`](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/__init__.py),
[`_dynamo/__init__`](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_dynamo/__init__.py),
[`convert_frame`](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_dynamo/convert_frame.py)
and [`trace_rules`](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_dynamo/trace_rules.py)
did not define a meta-path finder for this catalog. This is not an exhaustive
transitive import audit. No speculative Torch category is emitted.
Other hooks remain `unknown`, including unsupported vendored copies.
Only hashes/selectors are added, not copied upstream implementations.

## Observation Contract

At child termination, the first 32 actual finder entries are classified in
order. Exact object identity against the guard's original three-entry snapshot
yields `builtin`, `frozen` or `sealed`. Other entries require a static method
lookup, an ordinary Python function, its exact loaded module dictionary and
selected-root public file binding, and full code-structure equality against
one verified reference. The existing bounded comparator excludes filename
only; it includes nested constants, signed zero, first line, flags, line and
exception tables. No observed code is hashed or serialized. No finder method,
descriptor, equality, string conversion or representation is invoked.
Custom metaclasses are excluded before static lookup and remain unknown.

A match identifies that method structure and module binding, not all class
behavior, its globals' integrity, the first rejection, or authorization to
execute the hook. An arbitrary same-named class does not match. An unknown
finder does not emit its name, module, file, repr or code fingerprint.

The fixed report also distinguishes:

- Missing baseline identities **within the bounded scan**.
- Reordered or duplicated baseline entries.
- An exactly unchanged identity list.
- Scan truncation and comparison failure, separately.
- Pattern adapter state: unavailable, not-registered, pending, applying,
  applied, failed or unknown. This is the final state, not proof that a
  particular callback was reached before the first rejection.

The existing second FIFO transports exactly 44 bytes: two state bytes, ten
header bytes and 32 fixed one-byte entry IDs with padding. It is nonblocking,
explicitly passed, non-inheritable in the child, and closed by the existing
finally paths. Oversized, short, unknown-code or contradictory packets reject.
The report validator enforces exact fields, fixed source ordering, bounded
categories and consistency; no raw extension fields are admitted.

The ten original state comparisons, engine guard, shared five-stage application,
720/300/1080-second bounds, container resource/isolation policy and cleanup
behavior remain unchanged. The diagnostic's `entry_pass` additionally requires
an observed, unchanged identity list with no scan truncation or comparison
failure. A contradictory old bitset and new observation cannot claim a pass.
Diagnostic failure or unknown source coverage never turns an existing rejection
into success. Historical reports are not upgraded with inferred identities.

## Software Checks

Focused tests use original synthetic modules and subprocesses only. They
exercise all five fixed categories, source/selector failures, full-code
mismatches, signed zero, shadow/cached module identity, hostile attribute and
equality methods, list mutation classes, truncation, malformed packets,
publication validation and owned pipe cleanup. The four cached public source
files were independently rehashed and compiled as data: all five selectors
resolved uniquely. No upstream module, image, GPU, model or endpoint was run.

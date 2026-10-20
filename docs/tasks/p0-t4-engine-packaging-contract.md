# Exact-Image Packaging Contract

**Software-only, tested with synthetic files. No image execution or application.**
This is an explicit experimental contract for the two packaging differences
[measured by PR34](p0-t4-engine-record-diagnostic.md#recorded-outcome), not generic
wheel acceptance or permission to execute the engine. Candidate status remains
`BLOCKED`, `deployable:false`, `image:null`; native ABI, GPU and confidentiality
qualification are absent.

## Fixed Source And Image

The contract requires the existing Linux/amd64 manifest
`6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`, config
`bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d` and vLLM source
`5df63a7203cdb9328d824a27b682e77c7b9928be`. These are declared pinned inputs, not
independent image-origin or hardware attestation.

Bounded primary-source reads verified the existing pins:

- [pyproject.toml](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/pyproject.toml),
  SHA-256 `eaa19cba70f13234be4acdc1eeb2126b937c6f0892fd1af29863db5a62bee95b`.
- [plugin selection](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/plugins/__init__.py),
  SHA-256 `06942d64e526c2c7407265e1903d625c4d2118d8d7fc39c616b1cc6aa6fc2c8f`.

The exact parsed entry-point map is:

```ini
[console_scripts]
vllm = vllm.entrypoints.cli.main:main
[vllm.general_plugins]
lora_filesystem_resolver = vllm.plugins.lora_resolvers.filesystem_resolver:register_filesystem_resolver
lora_hf_hub_resolver = vllm.plugins.lora_resolvers.hf_hub_resolver:register_hf_hub_resolver
```

Names, groups and targets are case-sensitive. Defaults (including an empty
`[DEFAULT]`), duplicate sections/options, unknown entries, interpolation,
non-ASCII/control bytes and mismatched targets reject. This parses data only:
no plugin discovery, import, invocation or console execution occurs. The existing
partial launch plan retains `VLLM_PLUGINS=""`; the contract cannot change it.
The recorded image evidence contains group counts only, not these target values;
the targets above come from the independently hash-verified upstream source.

## Unlisted Bytecode Only

The measured image had 2,879 unlisted bytecode files and **zero listed/hashed
bytecode rows**. This change does not relax any listed-bytecode rule.

The experimental inspector still verifies every RECORD row and source hash. An
otherwise unlisted owned file must be beneath `vllm/`, in `__pycache__`, with a
CPython 3.12 cache name (optional `opt-1`/`opt-2`), and exactly one corresponding
source file whose hash was verified through RECORD. Sourceless, ambiguous,
unrecorded-source, wrong-interpreter, legacy unlisted `.pyc`, extra non-bytecode,
link and special-file cases reject. Classification is for deletion, never trust:
bytecode bytes are hashed as opaque data, not parsed, unmarshalled or executed.

Inspection returns a bounded, sorted removal plan binding each cache's path,
size/hash and its recorded source path/hash. The plan is sealed into the complete
before-inventory digest. Application revalidates the entire inspection and all
five patch-stage hashes before deletion. It rechecks each removed file, unlinks
with the existing anchored directory operations, and verifies the resulting
whole inventory **before any patch write**. Source/native changes or suppressed
deletions fail. All five stages retain their full inventory comparisons.

The application receipt adds a policy hash, plan hash, removed-unlisted count and
verified-removal status, without publishing paths or bytecode contents. Original
RECORD bytes remain unchanged; the receipt does not pretend the patched wheel is
the original distribution. Failed or interrupted application yields no successful
receipt; any partially modified build image must be discarded. This assumes a
quiescent trusted build filesystem, not atomic protection against concurrent
hostile filesystem writers.

## Deliberate Boundaries

The prepared source-bound context carries the fixed `experimental_packaging`
policy. Only an explicit offline API call with `packaging=True` uses it; default
`inspect()`/`apply_overlay()`, the CLI and the generated Docker recipe remain
strict. There is no new CLI override or executable runtime launcher.

Even the explicit API retains startup-hook, alternate-root, native-header,
inventory-budget, dependency, source-collision and five-stage checks. The actual
image's `.pth` files, populated alternate root, diagnostic-only inventory budget
and foreign-stub classifications are **not** admitted by this change. No roots
are deleted or hooks executed. Explicit import paths, `-I -S -B` worker propagation
and prevention of stale bytecode loading remain a separate reviewed contract.

## Verification

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_overlay.py' -k packaging -v
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Tests call real filesystem functions on synthetic trees and retain the generic
strict-mode regressions. No upstream package, image, GPU or model is executed.
No actual-image success claim or new acceptance evidence is produced here.

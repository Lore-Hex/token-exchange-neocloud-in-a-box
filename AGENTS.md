# AGENTS.md — instructions for coding agents

This repo is built mostly by coding agents (Codex) with a human owner (Joseph) who
approves spending and publishing. Read this file, then [PLAN.md](PLAN.md). PLAN.md is
the source of truth for scope, order and acceptance criteria. If this file and
PLAN.md disagree, stop and ask.

## How to pick work

1. Check the dependency table at the top of PLAN.md §8 for the phases that can
   start. Among them, take the lowest-numbered open task that is not waiting on
   a human gate.
2. If a task needs a gate that is not yet granted, prepare the request (see
   "Stop and ask" below), then move on to the next unblocked task.
3. One task = one branch = one pull request. Branch `p<phase>-t<task>-<slug>`,
   PR title `[P<phase>.T<task>] <summary>`.
4. A PR body has three sections: **What changed**, **How it was verified**
   (exact commands and `evidence/` paths), **Not done / follow-ups**.
5. Never merge your own PR. It merges after CI is green and review is clean.
6. When a task in PLAN.md turns out to be wrong (a vendor doc says otherwise, a
   version does not exist, a step cannot work), do not improvise around it.
   Write what you found, with sources, in the PR and stop.

## Your knowledge is out of date

The models this repo serves (GLM-5.3, DeepSeek V4.1) and most of the software
versions it pins were released in 2026. Do not rely on memory for model
architectures, engine flags, driver versions, kernel features or BIOS settings.
Read the upstream source (model card, `config.json`, release notes, vendor
deployment guide), cite it next to the setting (URL + section, in a comment or
the doc), and pin the exact version you read about.

Never guess a vendor flag, kernel parameter, BIOS option or engine argument. If
you cannot find a primary source, write `TODO(verify): <what and where to look>`
and stop that task.

## Evidence or it did not happen

Every acceptance criterion that involves real hardware, a real endpoint or a
benchmark is proven by a committed artifact under `evidence/`, produced by a
committed script:

```
evidence/p<phase>/<YYYY-MM-DD>-<slug>/
  run.json      # git sha, exact command line, UTC start/end, host facts, result: pass|fail
  output.log    # raw stdout/stderr, secrets scrubbed by scripts/scrub-secrets.sh
  ...           # result files (benchmark JSON, attestation bundles, checker reports)
```

- Never write a number in a doc, PR or README that is not backed by such an
  artifact. Estimates are allowed only when labelled **estimate** with their
  arithmetic shown.
- A failed run is evidence too. Commit it, say it failed, and say why if known.
- The README status table says **validated on <hardware>** only with evidence
  from that hardware. "Implemented", "tested against fakes" and "validated"
  are different claims; use the right one.

## Stop and ask (human gates)

Stop, write exactly what you need in the PR (and add a line to
[docs/gates.md](docs/gates.md)), and wait for Joseph when a step would:

- **Spend money or credits**: create, resize or keep running any VM, GPU, disk,
  bucket or reservation; rent bare metal; request quota. Prepare the exact
  command, the price per hour, the expected duration and the total.
- **Touch production TrustedRouter**: quill-router, quill-cloud-proxy,
  trustedrouter.com, api.trustedrouter.com, the marketplace, or any request
  through the TrustedRouter gateway with an API key (that spends credits).
  The provider checker runs directly against our own endpoint and does not
  need the gateway.
- **Publish**: releases, tags, container images, packages, DNS records,
  marketplace listings, posts.
- **Change settings**: GitHub repo settings, secrets, branch protection, cloud
  IAM, billing.
- **Contact a third party**: hardware providers, NVIDIA, Z.ai, DeepSeek.

Approval is per action. An approval for one run does not carry to the next.
Tear down rented resources when a run ends unless the approval says to keep
them, and record the teardown in the run's evidence.

## Secrets and data

- Never commit secrets, tokens, private keys, kubeconfigs, SSH keys, production
  hostnames or IPs, or any user data. Read credentials from environment
  variables documented in `.env.example`.
- `make check` runs a secret scanner. Keep it green; never add allow-list
  entries for real secrets.
- Never log prompts, completions, request or response bodies, headers or query
  strings — in any build, including debug builds and error paths. Benchmarks
  use synthetic prompts only.

## Security-critical code

The trust boundary is the confidential VM (Intel TDX trust domain) plus the
GPUs in confidential-computing mode. Everything that runs inside it is
security-critical: `image/`, `guest/`, and the attestation path.

- Keep dependencies inside the guest minimal and pinned by digest or hash.
- Go code that runs inside the guest uses the standard library only, like
  `trcs` in [trustedrouter-confidential-stack](https://github.com/Lore-Hex/trustedrouter-confidential-stack).
- Only the attested TLS front door listens on a non-loopback address. The
  inference engine binds to 127.0.0.1.
- Debug images (serial console, SSH, extra tools) must produce different
  measurements and must never appear in a release's accepted measurements.
- Pin everything that ends up in a measured image or model pack: base-image
  digests; kernel, driver, CUDA and engine versions; model revisions (Hugging
  Face commit SHA) and per-file SHA-256. No `latest`, no floating tags.

## Remote machines

- Hardware runs happen over SSH using committed scripts in `host/` and
  `scripts/`, never ad hoc commands you do not commit.
- Long jobs (weight downloads, image builds, benchmarks) run under `tmux` or
  `nohup` on the remote side with logs to a file. Poll the log; do not hold
  one SSH session open for hours.
- A machine with GPUs costs money every minute. Before starting a run, write
  down what you will do and how long it should take; stop the machine if the
  run stalls.

## Checks

```
make check      # lint, unit tests, doc link check, secret scan
make test-e2e   # GPU-free end-to-end test (fake engine + fake evidence)
```

Never skip, disable or loosen a test to get CI green. Find and fix the cause.

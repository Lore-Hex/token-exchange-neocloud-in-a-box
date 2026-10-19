# Security Policy

## Reporting a vulnerability

Use GitHub's **[private vulnerability reporting](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/security/advisories/new)**
for this repository. Reports stay visible only to maintainers while they are
investigated, and the same thread carries the fix and any advisory.

Please do not open a public issue for a suspected vulnerability, and do not
include real prompts, keys or other user data in a report.

Include what you can: the affected version or commit, the platform (Google
Cloud confidential VM, bare-metal TDX host, other), the hardware profile,
reproduction steps, and the impact you believe it has. A partial report sent
early is more useful than a complete one sent late.

## What to expect

- Acknowledgement within 72 hours.
- An assessment of severity and affected components within 5 business days.
- Notification when a fix ships, and credit in the advisory unless you ask us
  not to.

There is no bug bounty programme.

## Scope

[PLAN.md](PLAN.md) defines the intended security boundaries; the expanded
`docs/threat-model.md` is scheduled for P0.T2. Two limits in the plan are not vulnerabilities
in themselves: hardware attestation does not protect against an operator with
physical access to the machine, and a node is only as trustworthy as the
measurements its relying party chooses to accept.

Anything that lets a party outside the trust boundary (the confidential VM and
its confidential-mode GPUs) read request content, obtain the node's TLS key,
mint attestation evidence for a key or measurement it controls, alter model
weights without detection, or make the node report more protection than it
provides is in scope and is treated as critical.

This is pre-alpha software. Fixes target the main branch.

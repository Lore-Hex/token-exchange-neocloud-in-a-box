# Native Repeat Workflow Passed

One dispatch of [workflow 39851595140, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39851595140)
completed successfully at exact producer
`72aad43b1e3cb77c203d2620d7fca9c2e577dcf3`. This exercises the reusable workflow,
not just its unit tests or the earlier manually coordinated repeat builds.

| Job | GitHub job ID | UTC execution window | Outcome |
|---|---|---|---|
| First native build | 120078942802 | 20:45:33-20:49:34 | success |
| Second native build | 120080933322 | 20:49:37-20:55:38 | success |
| Metadata comparison | 120083868233 | 20:55:40-20:55:46 | success |

All times are on 2026-10-22. The two original builder receipts record narrower
build windows within their separate jobs. The same source commit and all 44
native builder/validator source hashes agree. The jobs are sequential; each
uses a fresh hosted runner and the same existing bounded builder.

The root GPT is 928,968,704 bytes with SHA-256
`1db9e647c887f1d7abdee8b8155f0f73dbbc475dfb08464d04872a15e3e33a08`;
the unsigned UKI is 97,898,496 bytes with SHA-256
`20b151d0c44719e6005e40dad1a329865ccc47bcd52a33370336518357d11609`.
The verity root is
`6b64b8e318419c17a7414e27978de9e51874489332ab947b42880ad8bca2e4af`.
All three match. Both builds report zero OOM kills, empty/removed owned cgroups
and private image deletion before metadata upload.

`first/` and `second/` preserve the five original JSON files per build. Their
`output.log` aliases are exact copies of `validation.json`, not invented logs.
`comparison.json` is the original comparator artifact; local replay with the
same expected source SHA matches it byte-for-byte. `workflow.json` is a public
GitHub API snapshot obtained with `gh run view --json`, preserving job identities
and times separately from the builder's own receipts. Regression tests pin all
original bytes, replay comparison and verify the retained producer sources.

This is a same-day trusted-builder repeat-build result. It does not prove
cross-platform/cross-day reproducibility or independent trust in GitHub's build
hosts. No image was published or retained. Original receipts keep signing,
boot, independent-builder and confidentiality qualifications false; this result
does not qualify the incomplete guest image as a live confidential service.

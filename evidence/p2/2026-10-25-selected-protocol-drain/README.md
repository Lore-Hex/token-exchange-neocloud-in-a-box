# Private protocol pass; active drain not established

`smoke.json` is the unmodified 5357-byte terminal receipt from source
`676c2a7b7344942f9d0e841518cb1f825854afc2`, produced by the committed
`host/record-selected-service-smoke.py --run --protocol-smoke` on the ordinary
eight-B300 node. SHA-256:
`a54d1b056d3fff4811448d52c002a20b5b4e722214120c10bece55f79f3e88b7`.
The operator observed this terminal result by 2026-10-25T01:24:30Z. This is not
a receipt timestamp; no duration or generic run envelope is synthesized.

All **17 unchanged provider-check assertions passed**, including native catalog,
callability, PONG, integer usage, model identity, finish reason, temperature,
forwarded fields, max-token spelling and all seven streaming assertions. The
initial authenticated request and both missing/wrong-key negatives passed.
All reported request usage is known. The subset streaming request used 34 prompt
and 180 completion tokens.

The enclosing run nevertheless **failed**. Its final drain request used 51 prompt
and 256 completion tokens, but did not reach an eligible first-content point:
`first_content=false`, no reader, no retirement request and no shutdown signal
for that active-stream test. Failure is `completion`. The receipt does not retain
reasoning-token counts or transport chunk boundaries; default reasoning and
late/buffered content cannot be distinguished from this evidence. This does not
prove that graceful shutdown is broken or that it works.

Normal failure cleanup drained and reaped TLS, guard and engine, and the host
reported `cleanup=confirmed`. A subsequent operator-only check found no running
Docker containers and zero GPU memory use. That check is not additional committed
machine-readable evidence. All public/deployable, hardware-validation, quality,
capability and privacy flags remain unchanged and false. `runtime_started=null`
is preserved exactly as emitted by the failed-host envelope.

## Source and artifact joins

`build.json` is the original build-only receipt, SHA-256
`3999ada0708cf27c34404b96475eb821e9dc201aa4f49062e0cb26467e0ae09d`.
`preparation.json` is the original public file-hash inventory, SHA-256
`6be2291143c7b86837720efd0490842e3d6b56c59e526180530ecf0212204b84`.
Its prepared-service digest is
`338d11d1d9a273281be0d2d5b4723c5844b1b08d633b534484209a0fa560b3fd`.
The local image ID is
`sha256:a546a5b349d3259aab040f9b8f5bac1e68a35417874d841739aae425c84908c3`.
All 793 preparation files and the build handoff were independently checked before
the run. The full context and private operator inputs are not published.
These are local daemon/OS-trusting receipts, not attestation or registry publication.

The historical protocol contract hash is
`d18de61288cb8bb872a4dade440b71f8bce4ae8cc58fcc3ecc1467d606e6d6e1`.
The subsequent drain-only chat-mode change has a different contract and requires
a fresh preparation, reviewed image and separately bounded run. It cannot repair
or replace this original failure. No automatic retry occurred.

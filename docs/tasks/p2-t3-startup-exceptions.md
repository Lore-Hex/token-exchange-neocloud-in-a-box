# Content-Free Startup Exceptions

The GPU lab now reuses the CPU entry diagnostic's exception encoder. Its
explicit nonblocking pipe carries one fixed-size packet: a fixed exception
category, at most eight source SHA-256/line pairs, and truncation flags. It
never formats exception messages, locals, source paths, stdout or stderr.
Sources outside the verified payload and configured interpreter roots are
not read. Existing CPU packet semantics and negative tests are unchanged.

The parent creates both pipes and explicitly passes their descriptors. The
child rejects standard streams, non-pipes, readable/blocking descriptors,
duplicate descriptor arguments and unexpected invocation shapes. No ambient
file descriptor is inferred. Both descriptors are closed before `serve.cmd`
can launch model workers; errors after that boundary still have only the
existing startup/exit observations. This is not general worker tracing.

Both functional and local-correctness GPU result schemas advance to v2.
The host verifies the encoder's source digest and rejects a diagnostic that
contradicts a ready engine. Missing, truncated or malformed observations cannot
qualify inference. No startup integrity gate, model hash check, network policy,
deadline or confidential eligibility has changed. Historical evidence remains
under its original schema and source revision.

Verification: `make check`, including the CPU packet privacy/malformed-input
tests, fixed pipe tests, descriptor lifetime tests and GPU receipt validation.
Actual GPU inference is still pending; this change alone proves none.

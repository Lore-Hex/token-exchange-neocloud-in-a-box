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
file descriptor is inferred. The checkpoint descriptor closes before
`serve.cmd`; the error descriptor stays non-inheritable in the parent server
until exit. This is not general worker tracing.

Pydantic model-validation errors can wrap the original validator traceback.
The encoder recognizes only the exact already-loaded `pydantic_core` class,
inspects at most eight errors without input or URL fields, and retains source
coordinates from wrapped built-in `ValueError`/`AssertionError` instances.
No validation dictionary, message, context, field location or input is emitted.
All tracebacks share the same 64-frame scan and eight-frame packet bounds;
unknown classes and subclasses cannot invoke diagnostic hooks. A diagnostic
failure sets the observer-failure bit and preserves the outer traceback.

Both functional and local-correctness GPU result schemas advance to v2.
The host verifies the encoder's source digest and rejects a diagnostic that
contradicts a ready engine. Missing, truncated or malformed observations cannot
qualify inference. No startup integrity gate, model hash check, network policy,
deadline or confidential eligibility has changed. Historical evidence remains
under its original schema and source revision.

Verification: `make check`, including the CPU packet privacy/malformed-input
tests, fixed pipe tests, descriptor lifetime tests and GPU receipt validation.
Actual GPU inference is still pending; this change alone proves none.

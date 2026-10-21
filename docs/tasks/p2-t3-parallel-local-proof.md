# P2.T3: Bounded parallel local weight verification

Large model trials must hash every staged byte before starting the engine.
The existing `check-local` performs this serially. A 510 GB pack makes that
repeated verification a substantial part of each trial; no measured speedup
is claimed until the new option is exercised on the GPU host.

`nib model check-local --workers 8 <pack> <directory>` opts into up to eight
concurrent file readers, each with a fixed 256 KiB scratch buffer. The ordinary
CLI default and `CheckLocal` API remain one worker. Download staging retains
its existing serial final verification. The bounded DeepSeek lab explicitly
selects eight workers; it does not reuse a past hash result or skip any bytes.

The same exact manifest, complete initial and final inventories, anchored
no-follow opens, file identity/size/type checks, SHA-256 comparisons, and
context-aware reads are shared by both modes. Any error cancels remaining jobs;
every worker is joined before returning or performing the final inventory.
No paths or file content are added to diagnostics. This is still a local
integrity observation, not dm-verity, immutable storage, or confidentiality.

Tests compare proofs for every worker count, exercise the CLI and invalid bounds,
rerun all existing corruption/extra-file/symlink cases with one, two, and eight
workers, and use real goroutine barriers to prove eight-buffer/eight-reader
limits and joining after success, failure, or cancellation. Run
`go test -race ./internal/modelpack` and full `make check` before a lab trial.

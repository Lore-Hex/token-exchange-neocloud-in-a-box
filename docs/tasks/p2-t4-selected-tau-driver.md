# Selected scorer parent driver

`tau_selected_driver` connects the isolated selected scorer to the existing
three-role session. It is an internal component, not a live-launch command.
The enclosing launcher must verify its Docker attach command and independently
admit both live owner readiness reports before calling it.

The verified parent loader builds one source-hashed module closure for the
policy, guarded clients, owner channels, session and driver. Ambient modules
and cached bytecode cannot substitute different owner or exception classes.
Legacy paths and their deadlines are unchanged.

The driver checks the selected policy and original task assertion, then checks
the exact owner types, fresh state and matching readiness bindings before
launch. It forwards only private framed pipe messages. All count/generate
requests share the existing central budget; failures preserve reservations.
Neither prompts nor completions are included in diagnostics.

The fixed 960-second outer deadline includes a 60-second cleanup reserve;
the trial also reserves those 60 seconds inside each original owner expiry
and respects its 900-second maximum. It never renews an owner deadline.
An abnormal or hanging scorer exit invalidates a terminal reward without
erasing known usage. The child attachment is killed and reaped on failure;
the enclosing runner must still verify and remove the actual containers.
Before closing either owner pipe, the driver requests an explicit finish
acknowledgement, bounded by the original expiry and at most five seconds per
owner. The request counts must agree with the scorer's role counts on success.
A pending count or failed owner cannot be reported as finished. An acknowledgement
failure removes an otherwise successful reward without erasing usage; the other
owner is still given bounded shutdown. Actual acknowledgements are retained
separately from the final trial status. Results explicitly distinguish owner
acknowledgements and attachment reaping from container cleanup. Final CPU/GPU
receipts must still be joined by the enclosing launcher.

Tests exercise actual subprocess pipes with synthetic owner replies, timeout
and reap, abnormal post-result exit, bad mode/stale owner rejection, unknown
usage, content-free results and verified-loader type identity. This is not a
real tau score, hardware proof or confidential claim. Live GPU/CPU owner
launch integration and runtime admission remain required.

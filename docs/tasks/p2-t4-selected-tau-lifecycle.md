# Selected Scorer Lifecycle

The internal `tau_trial.run(..., selected_owners=...)` path reuses the same
image verification, isolated Docker creation, terminal inspection and owned
cleanup as the existing scorer. It accepts only the selected sealed plan and
private pipes supplied by an enclosing owner launcher. It is not a public
endpoint or an arbitrary model URL adapter.

`selected_owners` carries the frozen transport manifest, the original bounded
deadline, and borrowed candidate/support read and write descriptors with their
original expiry. The driver duplicates the pipes, constructs all coordinators
in one verified source closure, closes duplicates and restores the borrowed
descriptors' blocking modes. It does not renew worker expiry. The enclosing
launcher retains the original handles and is responsible for worker teardown.

An evaluated scorer result requires a normal scorer exit, known usage, actual
candidate and simulator calls, and both finish acknowledgements. The outer
record also requires verified terminal container identity and confirmed removal.
Later terminal or cleanup failures preserve already observed accounting but
cannot turn into a pass. Dollar cost is not estimated from token counts.

The readiness provenance and final CPU/GPU owner reports still need to be
joined by the enclosing launcher. A scorer result is not proof of those owners'
cleanup, benchmark qualification, production admission, or confidential compute.
No hardware score is claimed by this software change.

Regression coverage: `scripts/test_tau_selected_run.py` exercises descriptor
ownership, partial duplication failure, mode isolation, image/isolation gates,
failed scorer execution, terminal drift and cleanup failures using fake Docker.
The existing selected-driver tests cover real subprocess protocol exchanges.

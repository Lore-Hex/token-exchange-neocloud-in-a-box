# Draft recorder output, not accepted evidence

The real-model test passed, but `make check` rejected the recorder's metadata:
the task-zero label, timestamp spelling, execution enum and extra archive field
did not conform to `nib.evidence/v1`. The unchanged original metadata is retained
as `recorder-draft.json.txt`, not a validated run. `output.log` is the original
sanitized test output. No test or schema was weakened to accept it.

The fixed recorder must produce a new, schema-valid run before the pilot can
claim accepted evidence. This directory is retained only for diagnosis.

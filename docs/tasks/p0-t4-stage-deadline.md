# Large Weight Transfer Deadline

The October 21 B300 lab staging run failed after 62 minutes, with 81 complete
files and two partial 101 GB Engram files. Its three-hour whole-run deadline
had not expired. Inspection found an independent `http.Client.Timeout` of one
hour, which includes the response body. This is consistent with the observed
failure after initial hashing plus one hour of transfer; the old recorder
discarded the child error, so the artifact alone does not identify the error.

Use the validated staging context as the single transfer deadline. Every HTTP
request, redirect and body read inherits it. The HTTP helper rejects a missing
deadline or one more than 24 hours away. Header timeout, explicit byte budget,
redirect allowlist, SHA-256 verification, exclusive publication and partial-file
cleanup are unchanged. Failed transfers remain failures; nothing is retried
automatically and no partial file is accepted as a completed weight.

Regression tests prove a three-hour deadline reaches the transport unchanged,
body cancellation remains effective, and invalid deadlines cannot reach the
network. These are software tests, not a successful full-model download or an
inference result. A fresh bounded hardware run is still required.

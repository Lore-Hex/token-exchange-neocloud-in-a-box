# Selected-Checkpoint Static Prefix Count

The committed recorder at `68ab473fda5b29bf85be07099494702b63aeba69`
ran on Linux/amd64 on 2026-10-24, from 12:19:51 to 12:23:10 UTC. Original
[receipts](../../evidence/p2/2026-10-24-selected-prefix-count/result/output.log)
are retained byte-for-byte, including the initial incomplete marker and both
container cleanup reports.

The pinned original tau retail task `2` has **4,785 static prefix tokens**:
one system message and 16 tools. With the 256-token output reserve, that is
**5,041 tokens**, exceeding a 4,096-token context before the first actual user
message. Four tokenizer controls and the non-stripping user-boundary check
passed. Both containers were removed with confirmed cleanup.

This run explicitly binds the selected abliterated checkpoint revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a` and its complete public file manifest
to the two tokenizer inputs. It did not load/check the model's weight files,
generate an answer, run a tau trial, contact an endpoint or use a GPU. The inner
CPU diagnostic's `BLOCKED` deployment status is preserved, not converted into
hardware qualification.

The result justifies rejecting 4K, not claiming that the full conversation fits
8K. The independently admitted 8K runtime and count-before-every-request path
must still reject longer histories without truncating instructions or tools.

Verification: `test_selected_prefix_evidence.py` pins all five original files,
checks historical source/model/tokenizer identities and replays the original
prefix validator with negative mutations. The secret-scanner exception allows
only five exact nonsecret token-sequence hashes at the exact receipt field and
path; same-line secrets, unknown hashes and wrong paths remain detected.

# Original cache-restore receipt

The committed `host/cache-public-weights.py` producer at
`a6c9d95a8c1286c0751d974003bddd33a0d1650b` emitted this single JSON receipt.
It is retained byte-for-byte as both `run.json` and `output.log`, without
inventing a different evidence envelope. The producer's source/supervisor
hashes and UTC interval are inside the original receipt.

All 83 files, 510,311,045,295 bytes, passed source hashing, copying and
destination hashing against the pinned manifest. The original read-only cache
was retained. The pure-I/O worker completed and its exit was confirmed;
`process_group_absence_verified=false` is preserved, not rewritten as a stronger
cleanup claim. This is weight-file preparation only, not inference, model
quality, confidentiality or proof of a continuously running service.

# Debug Network Object-Count Failure

**Result: fail.** One CPU-only TCG debug boot of producer
`987b0124be192dd0a27d8a8c05321aa9bc95fc52`, recorded by the committed
`host/run-debug-boot.py` in [run 40180212080](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40180212080).
The four JSON files are unchanged downloaded artifacts. `output.log` is an
exact copy of the content-free independent `validation.json`, not raw guest
output. The preservation test pins every original file's SHA-256.

The native image export and debug assembly passed. The initrd proof arrived,
but the required network service exited with status **96 / readback-count**;
root and complete proofs were absent. This establishes a mismatch against the
required 15 objects, **not the actual count or ruleset contents**. The run
retained neither. No successful boot, connectivity, production service,
confidentiality, hardware qualification or reproducibility is claimed.

The worker's `private_work_removed=false` precedes independent cleanup. The
final validator proves `private_images_removed=true`; the resource receipt
proves an empty, removed cgroup and zero OOM kills. QEMU's process group was
cleaned. No retry followed this failure.

Source inspection subsequently identified an error in the generated command:
nftables 1.0.9 expands table children only for `CMD_ADD`, not `CMD_CREATE`.
The old `create table inet nib { ... }` syntax therefore does not submit its
child chains, sets or rules. This is a source-backed explanation consistent
with the observed rejection, not a recovered dump of this boot's ruleset.
See the [fix and validation limits](../../../docs/tasks/p3-t1-guest-network.md#exclusive-table-create-correction).

# Debug Network Boot Pass

**Result: pass, CPU-only software boot.** One bounded TCG boot of producer
`a1427d7e7e6d7fdd337badcc6c77987ff61e9ed5`, recorded by the committed
`host/run-debug-boot.py` in [run 40184699878](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40184699878).
The four JSON receipts are unchanged downloaded artifacts. `output.log` is
an exact copy of the content-free independent validator, not raw guest output.
The preservation test pins their hashes and every recorded producer source.

The native export, unsigned debug assembly, initrd, root and completion proofs
passed. The source-bound network service is a required root-probe dependency:
this run exercised the exclusive table-create correction and strict complete
ruleset readback without weakening the firewall gate. QEMU used TCG, no NIC,
no GPU and no hardware confidential-computing acceleration. This is not a
connectivity test, production service, GPU/TEE qualification, signed release
or reproducibility comparison.

The worker's `private_work_removed=false` precedes independent outer cleanup
and is preserved as recorded. The final validator confirms all private images
were removed; the resource receipt confirms the bounded cgroup was empty and
removed, with zero OOM kills. The recorded memory peak reached the 2 GiB cap;
the successful result does not establish additional memory headroom.

The earlier [object-count failure](../2026-10-25-debug-network-count/README.md)
remains unchanged. This new run proves the corrected guest path, not recovery
of the earlier missing ruleset. Hardware and production gates remain open.

# Original Guest Network Readback Failure

One authorized hosted CPU-only [run 39897633422](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39897633422)
used exact producer `4f957525879bbf463db84b81e3f54ab7f053bad2` with
`debug_boot=true` and `guest_network=true`. Created 2026-10-23T03:56:10Z;
debug execution 04:02:48Z-04:04:27Z. This run **failed**. No retry was made.

## Preserved Originals

The four JSON files are byte-for-byte original cleanup-gated artifact members.
`output.log` duplicates `validation.json`, not serial, nft, installer or workflow
logs. `github-run.json` and `github-artifacts.json` preserve structured API
responses. No binary image or raw diagnostic content was downloaded.

Artifact `native-debug-boot-39897633422-1`, id `12478110237`, was downloaded
privately as an original 11,422-byte ZIP. Its SHA-256 matches GitHub's digest:
`88ac26c66b76cede0351c7ec41d3b8af7ca2a016ef0164716388a603b610ca77`.
The private archive is not published here. Before extraction, exactly four
nonempty regular mode-0600 JSON members were verified, each within 64 KiB,
without duplicate names, directories, symlinks, encryption or other members.
Extraction used exclusive no-follow files under a new private directory.

## Observations And Limits

- Authenticated fetch and native private export passed. Debug UKI assembly
  passed and binds the original UKI, probe and root hash.
- All five QEMU startup checks passed. The fixed proof reports INITRD true,
  root/complete false, `NETWORK_SERVICE`, and `readback` exit status 76
  (`exited`, `exit-code`), with no protocol error.
- Exact producer source closures contain 68 native and 30 debug entries;
  every SHA-256 was replayed against the producer's Git blobs. The independent
  parent-captured producer matches both worker closures.
- QEMU exited zero with no stdout/stderr bytes and its process group removed.
  These facts do not override the failed guest proof.
- Parent resource receipt reports child exit 1, peak 2,147,483,648 bytes,
  OOM kills 0, cgroup populated 0, empty verified and group removed.
  Limits remain 2 GiB, two CPUs, 128 PIDs and zero swap. Parent validation
  confirms all private images removed and metadata-only output.
- `private_work_removed=false` in the subordinate worker is unchanged:
  it does not claim the parent cleanup performed afterward.

Source `applyNetwork` maps status 76 only after the final nft command returned
success and `nftables.Verify` rejected its bounded ruleset. Earlier initial
empty-ruleset/check/create gates were passed. The original contains neither
ruleset content nor the specific failed comparison. No unique lower-level
cause, accepted firewall, connectivity, root success or corrected runtime is
claimed. Added fixed diagnostics are for a future separately authorized run,
not retroactive observations of this one.

This is nonconfidential TCG software evidence, not full guest-service, routing,
TDX/GPU, GCP compatibility, signing, reproducibility or production qualification.
The retained Spheron host and its separate GPU work were untouched.

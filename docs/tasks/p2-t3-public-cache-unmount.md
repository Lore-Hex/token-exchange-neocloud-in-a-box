# P2.T3: Retain the Public Weight Cache

`host/cache-public-weights.py --unmount-only` detaches a previously approved
public-weight NFS mount before its lab VM is retired. It does not delete the
volume, files or VM, and does not modify fstab. Provider-side volume attachment
and VM retirement remain separate operator actions.

The operator supplies the exact expected export and mount path, an exclusive
receipt path outside the mount, and the committed producer revision. The script
rejects symlinked paths, the root directory, a different source, a non-NFS
filesystem or missing `nodev,nosuid,noexec`. It uses ordinary `umount`, never
force or lazy unmount: a busy mount fails closed. Success also requires
`findmnt` to report no mount at that exact path. Each subprocess has a 45-second
timeout; there are no retries.

The receipt is reserved before unmounting and contains no export, host address,
path or raw exception. It reports mount removal only, not provider detachment,
volume persistence, billing termination or inference qualification. The
administrative lab host must not concurrently change the mount while this
operation runs; this is not an adversarial-host attestation mechanism.

```sh
python3 -I -S -B host/cache-public-weights.py --unmount-only \
  --export "$APPROVED_PUBLIC_EXPORT" --mount "$APPROVED_CACHE_MOUNT" \
  --output "$NEW_RECEIPT_PATH" --producer "$COMMITTED_REVISION"
```

Synthetic tests cover exact readback, busy mounts, failed/ambiguous absence
checks, symlinks, receipt collisions, output placement and retained source
contents. No real unmount or cloud cleanup is claimed by those tests.

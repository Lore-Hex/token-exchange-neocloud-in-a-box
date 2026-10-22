# P3.T1: Explicit Guest Driver Installation

The native image workflow has a separate, default-off `install_driver` input.
It requires `compile_driver=true`. Installation runs only after the five pinned
NVIDIA R595.91.07 modules compile against kernel 6.8.0-147 and the strong Linux
crypto references pass. It uses the same offline, resource-limited namespace,
private root, capability drop and seccomp policy. No `CAP_SYS_MODULE`, host
module path, network or module loading is added.

The pinned DKMS 3.4.3 `do_install` path installs into Ubuntu's `updates/dkms`
directory and runs `depmod`. Source review covers `read_conf`,
`read_framework_conf`, `do_install`, dependent-module handling and
post-transaction hooks. The package's NVIDIA config declares no install hooks
or dependent modules. Its framework config is comments only. The package also
ships a pinned `autoinstall_all_kernels.conf`; the existing `no-autoinstall`
sentinel prevents automatic installation. We pin
these exact files and require that `/etc/dkms` contains only the authenticated
package configuration and our existing controls; unexpected overrides are
rejected. Only NVIDIA may have a DKMS directory.
Module loading remains disabled by the reviewed framework control and the
unchanged capability/seccomp boundary.

Reviewed inputs are the authenticated packages in
[`r595-ubuntu2404.json`](../../image/inputs/r595-ubuntu2404.json): DKMS
`1:3.4.3-1ubuntu1` and `nvidia-dkms-open 595.91.07-1ubuntu1`.
Their file hashes are checked immediately before installation.
[Upstream depmod documentation](https://man7.org/linux/man-pages/man8/depmod.8.html)
describes the dependency indexes; our invocation goes through pinned DKMS for
the explicit target kernel, never the host's running kernel.

Acceptance requires byte-identical source and installed modules, exactly five
installed objects, one dependency-index entry for each, and nonempty bounded
binary indexes. Both merged-/usr and ordinary /lib layouts are handled without
following arbitrary symlinks. Install diagnostics remain bounded and exported.

An installation result is not module loading, runtime LKCA verification, a
bootable image, reproducibility, Secure Boot signing, CPU/GPU attestation or a
deployable service. Original compilation-only evidence remains unchanged.
Synthetic tests are not a real installation claim; the opt-in native workflow
must provide that evidence after all local and hosted gates pass.

The [first native installation attempt](p3-t1-dkms-package-config.md) exposed
the omitted package configuration. Its failed evidence is retained unchanged.

The subsequent [native installation pass](p3-t1-driver-install-pass.md) verified
all five installed modules and dependency indexes with independent cleanup.
The loading, boot and confidentiality limits above remain unchanged.

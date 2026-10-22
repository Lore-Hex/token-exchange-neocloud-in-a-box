# SPDX-License-Identifier: Apache-2.0
"""Fixed offline startup policy for the private, nonbootable runtime candidate."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

ROOTFS = Path("/work/output/rootfs")
PODMAN = ("podman.service", "podman.socket", "podman-restart.service",
          "podman-auto-update.service", "podman-auto-update.timer", "podman-kube@.service")
SYSTEM = (*PODMAN, "podman-clean-transient.service", "getty@.service", "serial-getty@.service",
          "console-getty.service", "container-getty@.service", "autovt@.service",
          "getty-static.service", "debug-shell.service", "rescue.service", "emergency.service",
          "systemd-logind.service", "dbus-org.freedesktop.login1.service",
          "user@.service", "user-runtime-dir@.service", "systemd-user-sessions.service",
          "systemd-pstore.service", "systemd-journald@.service", "systemd-journald@.socket",
          "systemd-journald-varlink@.socket", "nvidia-cdi-refresh.service", "nvidia-cdi-refresh.path")
USER = (*PODMAN, "dirmngr.service", "dirmngr.socket", "keyboxd.service", "keyboxd.socket",
        "gpg-agent.service", "gpg-agent.socket", "gpg-agent-browser.socket",
        "gpg-agent-extra.socket", "gpg-agent-ssh.socket")
FILES = {
    "etc/systemd/journald.conf.d/00-nib-no-content.conf":
        b"[Journal]\nStorage=none\nForwardToSyslog=no\nForwardToKMsg=no\nForwardToConsole=no\nForwardToWall=no\n",
    "etc/systemd/system.conf.d/00-nib-no-coredumps.conf": b"[Manager]\nDefaultLimitCORE=0\n",
    "etc/systemd/system-preset/00-nib-default-deny.preset": b"disable *\n",
    "etc/systemd/user-preset/00-nib-default-deny.preset": b"disable *\n",
}
LINKS = {"etc/systemd/" + scope + "/" + name: "/dev/null"
         for scope, names in (("system", SYSTEM), ("user", USER)) for name in names}
# Ubuntu's packaged syslog.conf sorts after our drop-in and enables forwarding.
LINKS["etc/systemd/journald.conf.d/syslog.conf"] = "/dev/null"


def require(value):
    if not value:
        raise ValueError("runtime-policy-rejected")


@contextmanager
def parent(root_fd, name, *, create):
    # Only fixed policy paths, anchored at the already opened private root.
    require(name in FILES or name in LINKS)
    current = os.dup(root_fd)
    try:
        for part in name.split("/")[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        yield current, name.rsplit("/", 1)[1]
    finally:
        os.close(current)


def inspect(root_fd, name):
    """Existing output must already be exactly ours; never replace vendor files."""
    with parent(root_fd, name, create=True) as (directory, leaf):
        try:
            info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if name in LINKS:
            require(stat.S_ISLNK(info.st_mode) and os.readlink(leaf, dir_fd=directory) == LINKS[name])
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_size == len(FILES[name]) and stat.S_IMODE(info.st_mode) == 0o644)
            fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                after = os.fstat(stream.fileno())
                require((after.st_dev, after.st_ino, after.st_mode, after.st_nlink, after.st_size) ==
                        (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size))
                require(stream.read(len(FILES[name]) + 1) == FILES[name])
        return True


def apply(root=ROOTFS):
    # No arbitrary-root CLI. This function is used only after isolated package
    # installation/driver work, before the build sandbox is discarded.
    require(root == ROOTFS)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        names = sorted(set(FILES) | set(LINKS))
        require(len(names) == len(FILES) + len(LINKS))
        present = {name: inspect(fd, name) for name in names}
        for name in names:
            if present[name]:
                continue
            with parent(fd, name, create=False) as (directory, leaf):
                if name in LINKS:
                    os.symlink(LINKS[name], leaf, dir_fd=directory)
                else:
                    output = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o644, dir_fd=directory)
                    with os.fdopen(output, "wb") as stream:
                        stream.write(FILES[name])
                        stream.flush()
                        os.fchmod(stream.fileno(), 0o644)
                        os.fsync(stream.fileno())
        require(all(inspect(fd, name) for name in names))
        return {"schema": "nib.runtime-startup-policy/v1", "result": "pass",
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(FILES.items())},
                "masks": dict(sorted(LINKS.items())), "services_started": False,
                "boot_verified": False, "complete_release_hardening": False}
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Private build helper only; never apply to a running host.")

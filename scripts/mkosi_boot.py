#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Private initrd/unsigned UKI assembly, never boot, sign or export an image."""
from contextlib import contextmanager
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import runpy
import shutil
import socket
import stat
import subprocess
import sys
import types

ROOTFS = Path("/work/output/rootfs")
WORK = Path("/work/boot-uki")
INPUTS = Path("/nib/boot-inputs.json")
KERNEL = "6.8.0-147-generic"
EPOCH = 1792454400
MAX_TREE = 192 << 20
MAX_IMAGE = 256 << 20
MAX_ENTRIES = 8192
OPERATIONS = {"depmod": 60, "cpio": 120, "ukify": 120, "inspect": 60}
LOGS = tuple("boot-" + op + "." + stream for op in OPERATIONS for stream in ("stdout", "stderr"))
# Exact executable/source members of the already authenticated Ubuntu packages.
TOOLS = {
    "usr/bin/cpio": (140928, "d342b752fcab2d8d6af40a548bcd6ba096ab34c52c53b608618f55950734ffe5"),
    "usr/bin/kmod": (174328, "5abf732e561c8be9ccd1794940b5d43025b041d4bed1db34782d979e0accc36f"),
    "usr/bin/ukify": (56179, "62b033782cf535e8633d82e4b6f4792352578da86a8bf0984beeaffd68b3c382"),
    "usr/lib/python3/dist-packages/pefile.py":
        (292005, "f99468cfed1930e8e9036a4bf4fcb9b23acb28c4e5421e215ce352a5ccdc811f"),
    "usr/lib/python3/dist-packages/ordlookup/__init__.py":
        (784, "a95df6e43bea93d1d81a517605792348053c36202a1a4f7083a6dc180ec33026"),
    "usr/lib/python3/dist-packages/ordlookup/oleaut32.py":
        (10877, "8346cd7072d1b87fe75bbe71a996ed6593564eb39505b74457c5bbbf1cf43ae7"),
    "usr/lib/python3/dist-packages/ordlookup/ws2_32.py":
        (3265, "832f959ebb7ac959b337cb1ba8b40449a370167676a238782ec880ab9203aee0"),
}
GUEST = {
    "boot/vmlinuz-" + KERNEL:
        (15083912, "41ba36d860aa0b0cd995f4ca8856d80249b244577e704929b55be13fbd1ebd2f"),
    "usr/lib/systemd/boot/efi/linuxx64.efi.stub":
        (68608, "799551ebda600e8358612a1d15805df5311210243a6ee69178aa27ff0a195d4e"),
}
DEPMOD_FILES = frozenset("""modules.alias modules.alias.bin modules.builtin.alias.bin
modules.builtin.bin modules.dep modules.dep.bin modules.devname modules.softdep
modules.symbols modules.symbols.bin""".split())
MASKS = ("emergency.service", "rescue.service", "debug-shell.service", "getty@.service",
         "serial-getty@.service", "console-getty.service", "systemd-networkd.service",
         "systemd-networkd.socket", "systemd-resolved.service")


def require(value):
    if not value:
        raise ValueError("boot-uki-rejected")


def module(name):
    return types.SimpleNamespace(**runpy.run_path("/nib/" + name + ".py", run_name="boot_" + name))


@contextmanager
def directory(path):
    require(path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def path_name(name):
    require(type(name) is str and 0 < len(name) <= 1024 and name == Path(name).as_posix()
            and not name.startswith("/") and len(name.split("/")) <= 32
            and all(p not in ("", ".", "..") for p in name.split("/"))
            and all(32 < ord(c) < 127 for c in name))
    return name


def read(path, limit, *, observation=None):
    with directory(path.parent) as fd:
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            if observation is not None:
                observation.update(regular=stat.S_ISREG(before.st_mode), links=before.st_nlink,
                                   bytes=before.st_size)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit)
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
            require(len(raw) == before.st_size and key(before) == key(after))
            return raw


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def write(path, raw, mode=0o600):
    with directory(path.parent) as fd:
        target = os.open(path.name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, mode, dir_fd=fd)
        with os.fdopen(target, "wb") as stream:
            stream.write(raw)
            os.fchmod(stream.fileno(), mode)


def verify_tools():
    result = {}
    for label, root, pins in (("tools", Path("/"), TOOLS), ("guest", ROOTFS, GUEST)):
        require(not os.path.lexists(root / "etc/ld.so.preload"))
        for name, (size, digest) in pins.items():
            value = identity(read(root / name, size))
            require(value == {"bytes": size, "sha256": digest})
            result[label + "/" + name] = value
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def inventory():
    raw = read(INPUTS, 2 << 20)
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(value["schema"] == "nib.initrd-inputs/v1" and value["kernel"] == KERNEL
            and value["native_execution_verified"] is False)
    members = value["members"]
    require(type(members) is dict and 0 < len(members) <= MAX_ENTRIES)
    total = 0
    for name, row in members.items():
        path_name(name)
        require(type(row) is dict and row["kind"] in ("file", "dir", "link")
                and type(row["mode"]) is int and 0 <= row["mode"] <= 0o777)
        if row["kind"] == "file":
            require(set(row) == {"kind", "mode", "bytes", "sha256"}
                    and type(row["bytes"]) is int and 0 <= row["bytes"] <= 64 << 20
                    and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]))
            total += row["bytes"]
        elif row["kind"] == "link":
            require(set(row) == {"kind", "mode", "target"} and type(row["target"]) is str
                    and 0 < len(row["target"]) <= 1024 and all(32 < ord(c) < 127 for c in row["target"]))
        else:
            require(set(row) == {"kind", "mode"})
    require(total <= MAX_TREE)
    return members, identity(raw)


def assemble(members, diagnostics=None):
    tree = WORK / "tree"
    tree.mkdir(mode=0o700)
    for name, row in sorted(members.items(), key=lambda item: (item[0].count("/"), item[0])):
        target = tree / name
        with directory(target.parent) as fd:
            if row["kind"] == "dir":
                os.mkdir(target.name, row["mode"], dir_fd=fd)
                os.chmod(target.name, row["mode"], dir_fd=fd)
            elif row["kind"] == "link":
                # Links are inert archive entries, never followed during assembly.
                os.symlink(row["target"], target.name, dir_fd=fd)
            else:
                if diagnostics is not None:
                    diagnostics.clear()
                    diagnostics.update(member=name, expected_bytes=row["bytes"],
                                       expected_sha256=row["sha256"], observed={})
                raw = read(ROOTFS / name, row["bytes"],
                           observation=diagnostics["observed"] if diagnostics is not None else None)
                require(identity(raw) == {k: row[k] for k in ("bytes", "sha256")})
                write(target, raw, row["mode"])
    if diagnostics is not None:
        diagnostics.clear()
    # mkosi ecf7f570 configure_initrd: PID1 symlink and initrd-release marker.
    for name in ("etc", "etc/systemd", "etc/systemd/system", "etc/systemd/journald.conf.d",
                 "dev", "proc", "sys", "run", "tmp", "sysroot"):
        with directory((tree / name).parent) as fd:
            os.mkdir(Path(name).name, 0o755, dir_fd=fd)
            os.chmod(Path(name).name, 0o755, dir_fd=fd)
    links = {"init": "/usr/lib/systemd/systemd", "etc/os-release": "../usr/lib/os-release",
             "etc/initrd-release": "os-release",
             "etc/systemd/system/default.target": "/usr/lib/systemd/system/initrd.target"}
    links.update({"etc/systemd/system/" + name: "/dev/null" for name in MASKS})
    # Same basename override as the installed-root policy, preserving vendor data.
    links["etc/systemd/journald.conf.d/syslog.conf"] = "/dev/null"
    for name, target in links.items():
        with directory((tree / name).parent) as fd:
            os.symlink(target, Path(name).name, dir_fd=fd)
    write(tree / "etc/systemd/journald.conf.d/99-nib.conf",
          b"[Journal]\nStorage=none\nForwardToSyslog=no\nForwardToKMsg=no\nForwardToConsole=no\n", 0o644)
    write(tree / "etc/passwd", b"root:x:0:0:root:/root:/usr/sbin/nologin\n", 0o644)
    write(tree / "etc/group", b"root:x:0:\n", 0o644)
    write(tree / "etc/machine-id", b"", 0o444)
    write(WORK / "ukify.conf", b"[UKI]\n", 0o400)
    write(WORK / "depmod.conf", b"# Fixed empty configuration.\n", 0o400)


def snapshot():
    tree, result, total = WORK / "tree", {}, 0
    for parent, dirs, files in os.walk(tree, followlinks=False):
        require(len(result) + len(dirs) + len(files) <= MAX_ENTRIES)
        for leaf in sorted(dirs + files):
            path = Path(parent) / leaf
            name = path_name(path.relative_to(tree).as_posix())
            info = path.lstat()
            row = {"mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(kind="link", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind="dir")
            else:
                raw = read(path, 64 << 20)
                total += len(raw)
                require(total <= MAX_TREE)
                row.update(kind="file", **identity(raw))
            result[name] = row
    return result


def cmdline(root_hash):
    require(type(root_hash) is str and re.fullmatch(r"[0-9a-f]{64}", root_hash))
    # systemd v255.4 veritysetup-generator determine_device discovers the two
    # GPT PARTUUIDs from the root hash; fstab-generator mounts /dev/mapper/root.
    return ("root=/dev/mapper/root rootfstype=erofs ro roothash=" + root_hash).encode()


def environment():
    return {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp", "TMPDIR": "/tmp",
            "LANG": "C", "LC_ALL": "C", "SOURCE_DATE_EPOCH": str(EPOCH)}


def operation(name):
    require(name in OPERATIONS)
    if name == "depmod":
        # kmod v31 tools/kmod.c dispatches compatibility commands by argv[0].
        return ["depmod", "--all", "--config", str(WORK / "depmod.conf"),
                "--basedir", str(WORK / "tree"), KERNEL]
    if name == "cpio":
        # Exact mkosi 27.1 archive.py make_cpio interface, with numeric ownership.
        return ["/usr/bin/cpio", "--create", "--reproducible", "--renumber-inodes", "--null",
                "--format=newc", "--quiet", "--directory", str(WORK / "tree"), "--owner=0:0"]
    if name == "ukify":
        return ["/usr/bin/ukify", "--config=" + str(WORK / "ukify.conf"), "--efi-arch=x64",
                "--stub=" + str(ROOTFS / "usr/lib/systemd/boot/efi/linuxx64.efi.stub"),
                "--linux=" + str(ROOTFS / ("boot/vmlinuz-" + KERNEL)),
                "--initrd=" + str(WORK / "initrd.cpio"), "--cmdline=@" + str(WORK / "cmdline"),
                "--os-release=@" + str(WORK / "tree/usr/lib/os-release"), "--uname=" + KERNEL,
                "--no-sign-kernel", "--no-measure", "--output=" + str(WORK / "nib.efi"), "build"]
    return []


def command(name, prefix):
    operation(name)
    args = ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py",
            "--ro-bind", "/", "/", "--ro-bind", str(ROOTFS), str(ROOTFS), "--tmpfs", "/tmp"]
    if name != "inspect":
        args += ["--bind", str(WORK), str(WORK)]
        if name != "depmod":
            args += ["--ro-bind", str(WORK / "tree"), str(WORK / "tree")]
        if name == "ukify":
            for leaf in ("initrd.cpio", "cmdline", "ukify.conf"):
                args += ["--ro-bind", str(WORK / leaf), str(WORK / leaf)]
    return [*args, "--chdir", "/", *prefix,
            "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/boot-uki.py", name]


def execute(processes, prefix, name, output):
    def logs(stdout, stderr):
        for stream, raw in (("stdout", stdout), ("stderr", stderr)):
            require(type(raw) is bytes and len(raw) <= 65536)
            if raw:
                write(output / ("boot-" + name + "." + stream), raw)
    try:
        result = processes.execute_process_group(command(name, prefix), env=environment(),
            timeout=OPERATIONS[name], max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
    except processes.OutputLimitExceeded as error:
        logs(error.stdout, error.stderr)
        raise
    logs(result.stdout, result.stderr)
    result.check_returncode()
    return result


class VendorSources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Execute pinned package sources, never cached bytecode or ambient packages."""
    def find_spec(self, fullname, path=None, target=None):
        names = {"pefile": "pefile.py", "ordlookup": "ordlookup/__init__.py",
                 "ordlookup.oleaut32": "ordlookup/oleaut32.py", "ordlookup.ws2_32": "ordlookup/ws2_32.py"}
        if fullname not in names:
            return None
        spec = importlib.util.spec_from_loader(fullname, self, is_package=fullname == "ordlookup")
        spec.origin = "/usr/lib/python3/dist-packages/" + names[fullname]
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, value):
        name = value.__spec__.origin.lstrip("/")
        size, digest = TOOLS[name]
        raw = read(Path("/") / name, size)
        require(identity(raw) == {"bytes": size, "sha256": digest})
        value.__file__ = value.__spec__.origin
        exec(compile(raw, value.__file__, "exec", dont_inherit=True), value.__dict__)


def fields(structure):
    return {name: getattr(structure, name) for aliases in structure.__keys__ for name in aliases}


def unchanged(actual, expected, mutable=()):
    left, right = fields(actual), fields(expected)
    require(left.keys() == right.keys()
            and all(left[name] == right[name] for name in left if name not in mutable))


def aligned(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def layout(pe, raw):
    header = pe.OPTIONAL_HEADER
    file_align, section_align = header.FileAlignment, header.SectionAlignment
    require(512 <= file_align <= 65536 and file_align & (file_align - 1) == 0
            and section_align >= file_align and section_align & (section_align - 1) == 0
            and (section_align >= 4096 or section_align == file_align)
            and 0 < header.SizeOfHeaders <= len(raw) and header.SizeOfHeaders % file_align == 0
            and header.SizeOfImage % section_align == 0
            and pe.FILE_HEADER.NumberOfSections == len(pe.sections))
    files, virtual, sections = [], [], {}
    for section in pe.sections:
        name = section.Name.rstrip(b"\0").decode("ascii")
        start, size = section.PointerToRawData, section.SizeOfRawData
        address, length = section.VirtualAddress, section.Misc_VirtualSize
        require(name not in sections and 0 < length <= size and start % file_align == size % file_align == 0
                and start >= header.SizeOfHeaders and start + size <= len(raw)
                and address % section_align == 0
                and address >= aligned(header.SizeOfHeaders, section_align)
                and address + aligned(max(size, length), section_align) <= header.SizeOfImage
                and section.get_file_offset() + section.sizeof() <= header.SizeOfHeaders)
        files.append((start, start + size))
        virtual.append((address, address + aligned(max(size, length), section_align)))
        sections[name] = section
    for ranges in (files, virtual):
        ordered = sorted(ranges)
        require(all(first[1] <= second[0] for first, second in zip(ordered, ordered[1:])))
    require(virtual and header.SizeOfImage == max(end for _, end in virtual))
    return sections


def merged_sbat(stub, kernel):
    # Pinned ukify merge_sbat: one fixed header, then stub and kernel records.
    rows = []
    for pe in (stub, kernel):
        for section in pe.sections:
            if section.Name.rstrip(b"\0") == b".sbat":
                lines = section.get_data().rstrip(b"\0").decode("utf-8").splitlines()
                require(lines and lines[0].startswith("sbat,"))
                rows.extend(lines[1:])
    return ("sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n"
            + "\n".join(rows) + "\n\0").encode()


def inspect_uki(pefile):
    raw = read(WORK / "nib.efi", MAX_IMAGE)
    pe = pefile.PE(data=raw, fast_load=True)
    require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Magic == 0x20b
            and pe.OPTIONAL_HEADER.Subsystem == 10 and not pe.get_warnings())
    security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
    require(security.VirtualAddress == security.Size == 0)
    sections = layout(pe, raw)
    stub_path = "usr/lib/systemd/boot/efi/linuxx64.efi.stub"
    stub_raw = read(ROOTFS / stub_path, GUEST[stub_path][0])
    require(identity(stub_raw) == dict(zip(("bytes", "sha256"), GUEST[stub_path])))
    stub = pefile.PE(data=stub_raw, fast_load=True)
    kernel_raw = read(ROOTFS / ("boot/vmlinuz-" + KERNEL), 16 << 20)
    kernel = pefile.PE(data=kernel_raw, fast_load=True)
    require(not stub.get_warnings() and not kernel.get_warnings()
            and stub.FILE_HEADER.PointerToSymbolTable == stub.FILE_HEADER.NumberOfSymbols == 0)
    for name in ("DOS_HEADER", "NT_HEADERS"):
        unchanged(getattr(pe, name), getattr(stub, name))
    unchanged(pe.FILE_HEADER, stub.FILE_HEADER, ("NumberOfSections",))
    unchanged(pe.OPTIONAL_HEADER, stub.OPTIONAL_HEADER,
              ("SizeOfInitializedData", "SizeOfImage", "CheckSum", "SizeOfHeaders"))
    require(pe.OPTIONAL_HEADER.SizeOfHeaders == aligned(stub.OPTIONAL_HEADER.SizeOfHeaders,
                                                       stub.OPTIONAL_HEADER.FileAlignment)
            and pe.OPTIONAL_HEADER.CheckSum == 0
            and len(pe.OPTIONAL_HEADER.DATA_DIRECTORY) == len(stub.OPTIONAL_HEADER.DATA_DIRECTORY))
    for actual, original in zip(pe.OPTIONAL_HEADER.DATA_DIRECTORY, stub.OPTIONAL_HEADER.DATA_DIRECTORY):
        unchanged(actual, original)
    originals = {s.Name.rstrip(b"\0").decode("ascii"): s for s in stub.sections}
    require(set(originals) == {".text", ".rodata", ".data", ".sbat", ".sdmagic", ".reloc"}
            and len(originals) == len(stub.sections) and originals.keys() <= sections.keys())
    shift = 0
    for original in stub.sections:
        name = original.Name.rstrip(b"\0").decode("ascii")
        actual = sections[name]
        mutable = ("PointerToRawData", "SizeOfRawData")
        if name == ".sbat":
            mutable += ("Misc", "Misc_PhysicalAddress", "Misc_VirtualSize")
        unchanged(actual, original, mutable)
        alignment = pe.OPTIONAL_HEADER.FileAlignment
        pointer = aligned(original.PointerToRawData + shift, alignment)
        size = aligned(original.SizeOfRawData, alignment)
        require(actual.PointerToRawData == pointer and actual.SizeOfRawData == size)
        shift += pointer - (original.PointerToRawData + shift) + size - original.SizeOfRawData
        data = merged_sbat(stub, kernel) if name == ".sbat" else original.get_data()
        require(actual.Misc_VirtualSize == (len(data) if name == ".sbat" else original.Misc_VirtualSize)
                and len(data) <= size and actual.get_data() == data + bytes(size - len(data)))
    entry = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    text = sections[".text"]
    require(text.VirtualAddress <= entry < text.VirtualAddress + text.Misc_VirtualSize
            and text.Characteristics & 0x20000000)
    wanted = {".linux": identity(kernel_raw),
              ".initrd": identity(read(WORK / "initrd.cpio", MAX_IMAGE)),
              ".cmdline": identity(read(WORK / "cmdline", 1024)),
              ".osrel": identity(read(WORK / "tree/usr/lib/os-release", 4096)),
              ".uname": identity(KERNEL.encode())}
    require(set(sections) == set(wanted) | set(originals))
    for name, expected in wanted.items():
        section = sections[name]
        require(identity(section.get_data()[:section.Misc_VirtualSize]) == expected
                and section.Characteristics == (0x40000020 if name == ".linux" else 0x40000040))
    require(pe.OPTIONAL_HEADER.SizeOfInitializedData == stub.OPTIONAL_HEADER.SizeOfInitializedData
            + sum(sections[name].Misc_VirtualSize for name in wanted))
    return {"schema": "nib.uki-sections/v1", "image": identity(raw), "sections": wanted,
            "signed": False}


def check_cpio(raw, expected):
    # Linux newc archive contract; verification only. GNU cpio writes the archive.
    require(0 < len(raw) <= MAX_IMAGE)
    offset, actual = 0, {}
    while True:
        require(offset + 110 <= len(raw) and raw[offset:offset+6] == b"070701")
        header = raw[offset+6:offset+110]
        require(re.fullmatch(b"[0-9a-fA-F]{104}", header))
        fields = [int(header[i:i+8], 16) for i in range(0, 104, 8)]
        _, mode, uid, gid, links, mtime, size, _, _, rmajor, rminor, namesize, checksum = fields
        require(0 < namesize <= 1025 and 0 <= mode <= 0xffff
                and uid == gid == rmajor == rminor == checksum == 0)
        start = offset + 110
        require(start + namesize <= len(raw) and raw[start+namesize-1] == 0)
        name = raw[start:start+namesize-1].decode("ascii")
        start = (start + namesize + 3) & ~3
        require(start + size <= len(raw))
        data = raw[start:start+size]
        offset = (start + size + 3) & ~3
        if name == "TRAILER!!!":
            require(size == 0 and not any(raw[offset:]) and actual == expected)
            return
        path_name(name)
        require(name not in actual and name in expected and mtime == EPOCH)
        row = {"mode": stat.S_IMODE(mode)}
        if stat.S_ISDIR(mode):
            require(size == 0)
            row.update(kind="dir")
        elif stat.S_ISLNK(mode):
            require(links == 1 and size > 0)
            row.update(kind="link", target=data.decode("ascii"))
        else:
            require(stat.S_ISREG(mode) and links == 1)
            row.update(kind="file", **identity(data))
        actual[name] = row


def build(processes, prefix, root_hash, output=Path("/out"), *, diagnostics=None, consume=None):
    require(consume is None or callable(consume))
    line = cmdline(root_hash)
    tools = verify_tools()
    members, inputs = inventory()
    with directory(WORK.parent) as fd:
        os.mkdir(WORK.name, 0o700, dir_fd=fd)
    try:
        assemble(members, diagnostics)
        before = snapshot()
        execute(processes, prefix, "depmod", output)
        after = snapshot()
        require(all(after.get(k) == v for k, v in before.items()))
        added = set(after) - set(before)
        require(added == {"usr/lib/modules/" + KERNEL + "/" + p for p in DEPMOD_FILES})
        for name in after:
            os.utime(WORK / "tree" / name, (EPOCH, EPOCH), follow_symlinks=False)
        write(WORK / "file-list", b"\0".join(p.encode() for p in sorted(after)) + b"\0", 0o400)
        write(WORK / "cmdline", line, 0o400)
        execute(processes, prefix, "cpio", output)
        archive = read(WORK / "initrd.cpio", MAX_IMAGE)
        check_cpio(archive, after)
        archive_id = identity(archive)
        del archive
        execute(processes, prefix, "ukify", output)
        result = execute(processes, prefix, "inspect", output)
        sections = json.loads(result.stdout, object_pairs_hook=unique_object)
        require(sections["schema"] == "nib.uki-sections/v1" and sections["signed"] is False
                and sections["sections"][".initrd"] == archive_id
                and sections["sections"][".cmdline"] == identity(line)
                and sections["image"] == identity(read(WORK / "nib.efi", MAX_IMAGE))
                and archive_id == identity(read(WORK / "initrd.cpio", MAX_IMAGE))
                and after == snapshot() and tools == verify_tools())
        report = {"schema": "nib.private-boot-uki/v1", "result": "pass", "kernel": KERNEL,
                  "root_hash": root_hash, "inputs": inputs, "initrd": archive_id, "uki": sections,
                  "tools": tools, "initrd_members": len(after), "archive_checked": True,
                  "sections_checked": True, "signed": False, "images_exported": False,
                  "boot_verified": False, "reproducibility_verified": False, "confidentiality_verified": False}
        if consume is not None:
            consume(report)
            require(sections["image"] == identity(read(WORK / "nib.efi", MAX_IMAGE)))
    finally:
        shutil.rmtree(WORK)
    require(not os.path.lexists(WORK))
    report["private_images_removed"] = True
    return report


def main():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and len(sys.argv) == 2 and os.geteuid() == os.getegid() == 0
            and socket.if_nameindex() == [(1, "lo")])
    name = sys.argv[1]
    args = operation(name)
    module("confinement").check()
    verify_tools()
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    limit = 0 if name == "inspect" else MAX_IMAGE
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    if name in ("ukify", "inspect"):
        require(not any(p == "pefile" or p.startswith("ordlookup") for p in sys.modules))
        sys.meta_path.insert(0, VendorSources())
        if name == "inspect":
            import pefile
            print(json.dumps(inspect_uki(pefile), sort_keys=True))
            return
        require(not os.path.lexists(WORK / "nib.efi"))
        raw = read(Path("/usr/bin/ukify"), TOOLS["usr/bin/ukify"][0])
        sys.argv = args
        # Compile the verified main script as well as its non-stdlib imports.
        value = types.ModuleType("__main__")
        value.__file__ = args[0]
        sys.modules["__main__"] = value
        exec(compile(raw, args[0], "exec", dont_inherit=True), value.__dict__)
        return
    if name == "cpio":
        with directory(WORK) as fd:
            source = os.open("file-list", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            target = os.open("initrd.cpio", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        os.dup2(source, 0)
        os.dup2(target, 1)
        os.close(source)
        os.close(target)
    os.execve("/usr/bin/kmod" if name == "depmod" else args[0], args, environment())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_BOOT_UKI_REJECTED")

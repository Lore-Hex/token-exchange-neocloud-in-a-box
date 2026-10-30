"""Closed metadata bootstrap protocol. No CLI, ambient transport or admission.

The raw admission digest is an independently supplied trust root, never derived
by this code as authorization. Default entry is blocked before any artifact.
"""

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import select
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import types

OPERATIONAL_ADMISSION_PIN = None
PRODUCER = "31162745cb90f4ab268f1b2b8eb8c768b750442a"
PUBLIC = {
    "cache": ("host/cache-public-weights.py", 45953, "25141adae8f894a54f4b8f90c65ba26a9d7b3f493121b640b381c4f2a0cb57f6"),
    "inspection": ("host/inspect-public-weight-cache.py", 25458, "370db5bdd583122b76809510500a2a0cb5c2a89e3217aae060cea984802a2cdb"),
    "resource": ("host/probe-build-resources.py", 11551, "2b740ef5b1ecbc87cb3a078fcbb081017f7db5c9600c9956f97cc52330ad9fdc"),
    "probe": ("host/probe.py", 7989, "dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff"),
}
CAPSULE_SHA = "3baec236b5418692beff94f3e181b945694e3201eb13bb65036251d2079070b7"
RUNNER_SHA = "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c"
SEED_KEYS = frozenset(PUBLIC) | {"adapter", "monitor", "capsule"}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"}


def require(value):
    if not value:
        raise ValueError("metadata-bootstrap-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"


def load(raw, name):
    module = types.ModuleType("metadata_bootstrap_bound_" + name)
    module.__file__ = "/authenticated-metadata-seed/" + name + ".py"
    exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return module


def validate_sources(sources, pins):
    require(type(sources) is dict and set(sources) == SEED_KEYS and type(pins) is dict
            and set(pins) == SEED_KEYS and sum(len(raw) for raw in sources.values()) <= 1 << 20)
    for name, raw in sources.items():
        require(type(raw) is bytes and 0 < len(raw) <= 65536
                and pins[name] == {"bytes": len(raw), "sha256": sha(raw)})
    for name, (_, size, digest) in PUBLIC.items():
        require(pins[name] == {"bytes": size, "sha256": digest})
    require(pins["capsule"]["sha256"] == CAPSULE_SHA)


def request_checked(raw, sources):
    # Authenticate monitor/adapter pins through admission/launcher BEFORE this call.
    m = load(sources["monitor"], "monitor")
    value = m.parsed(raw)
    require(set(value) == {"schema", "producer", "attempt_id", "parent", "parent_identity",
        "boot_id", "clock_namespace", "deadline_monotonic", "work_request_sha256",
        "prepared_sha256", "capsule_sha256", "seed", "python", "python_sha256",
        "target_evidence_sha256", "accounting_evidence_sha256"}
        and value["schema"] == "private.metadata-bootstrap-request/v2" and value["producer"] == PRODUCER)
    import re
    require(type(value["attempt_id"]) is str and re.fullmatch("[0-9a-f]{32}", value["attempt_id"]))
    for key in ("work_request_sha256", "prepared_sha256", "capsule_sha256", "python_sha256",
                "target_evidence_sha256", "accounting_evidence_sha256"):
        m.digest(value[key])
    require(type(value["boot_id"]) is str
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]))
    pair = value["clock_namespace"]
    require(type(pair) is list and len(pair) == 2 and all(type(x) is int and x >= 0 for x in pair)
            and pair[1] > 0)
    parent = value["parent_identity"]
    require(type(parent) is list and len(parent) == 4 and all(type(x) is int and x >= 0 for x in parent)
            and parent[1] > 0 and parent[2:] == [0, 0o700])
    for key in ("parent", "python"):
        path = value[key]
        require(type(path) is str and path.startswith("/") and not path.startswith("//")
                and re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
                and os.path.normpath(path) == path and ".." not in Path(path).parts)
    require(len(os.fsencode(control_root(value) / "installer.sock")) < 108)
    require(type(value["deadline_monotonic"]) in (int, float)
            and math.isfinite(value["deadline_monotonic"]) and value["deadline_monotonic"] > 0)
    validate_sources(sources, value["seed"])
    return m, value


def control_root(request):
    return Path(request["parent"]) / ("bootstrap-" + request["attempt_id"])


# The command contains only bounded metadata/pins and this fixed loader. All
# executable seed bytes travel raw, not compressed, through a bounded pipe.
SEED_LOADER = r'''
import hashlib,json,math,os,pathlib,selectors,stat,sys,time,types
def need(x):
 if not x: raise ValueError('bootstrap-seed-rejected')
def left():
 n=D-time.monotonic(); need(n>0); return n
def exact(n):
 need(0<=n<=1<<20); b=bytearray()
 os.set_blocking(0,False)
 with selectors.DefaultSelector() as s:
  s.register(0,selectors.EVENT_READ)
  while len(b)<n:
   need(s.select(left())); left()
   try: q=os.read(0,min(65536,n-len(b)))
   except (BlockingIOError,InterruptedError): continue
   need(q); b.extend(q)
 return bytes(b)
def out(v):
 b=json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+bytes((10,))
 need(len(b)<=4096); b=len(b).to_bytes(4,'big')+b; os.set_blocking(1,False)
 with selectors.DefaultSelector() as s:
  s.register(1,selectors.EVENT_WRITE)
  while b:
   need(s.select(left())); left()
   try: n=os.write(1,b)
   except (BlockingIOError,InterruptedError): continue
   need(n>0); b=b[n:]
def clock():
 boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 ns=os.stat('/proc/self/ns/time'); return {'boot_id':boot,'clock_namespace':[ns.st_dev,ns.st_ino]}
def identity():
 b=pathlib.Path('/proc/self/stat').read_bytes(); need(len(b)<=8192)
 f=b.rsplit(b')',1)[1].split()
 return {'pid':os.getpid(),'start_ticks':int(f[19]),'ppid':int(f[1]),'pgid':int(f[2]),'sid':int(f[3]),**clock()}
try:
 R=json.loads(RAW); D=R['deadline_monotonic']; left()
 need(sys.platform=='linux' and os.getuid()==os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
 need(clock()=={k:R[k] for k in ('boot_id','clock_namespace')})
 need(os.path.realpath(sys.executable)==R['python'])
 fd=os.open(R['python'],os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd); need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022 and 0<st.st_size<=16<<20)
  binary=os.read(fd,(16<<20)+1); need(len(binary)==st.st_size and hashlib.sha256(binary).hexdigest()==R['python_sha256'])
 finally: os.close(fd)
 out({'kind':'seed-owner','request_sha256':hashlib.sha256(RAW).hexdigest(),'owner':identity(),'monotonic':time.monotonic()})
 S={}
 for key in sorted(R['seed']):
  row=R['seed'][key]; need(type(row['bytes']) is int and 0<row['bytes']<=65536)
  S[key]=exact(row['bytes']); need(hashlib.sha256(S[key]).hexdigest()==row['sha256'])
 left()
 mod=types.ModuleType('authenticated_metadata_bootstrap'); mod.__file__='/authenticated-metadata-seed/adapter.py'
 exec(compile(S['adapter'],mod.__file__,'exec',dont_inherit=True),mod.__dict__)
 mod.remote_owner(RAW,CONSUME,S)
except BaseException:
 os._exit(1)
os._exit(0)
'''


def seed_program(raw_request, raw_consume):
    require(type(raw_request) is bytes and type(raw_consume) is bytes
            and len(raw_request) <= 16384 and len(raw_consume) <= 16384)
    program = "RAW=" + repr(raw_request) + "\nCONSUME=" + repr(raw_consume) + "\n" + SEED_LOADER
    require(len(program.encode()) <= 65536)
    return program


def worker_program(root, raw_request):
    # This executes only after the unchanged RESOURCE_BOOTSTRAP has joined.
    return "ROOT=" + repr(str(root)) + "\nRAW=" + repr(raw_request) + "\n" + r'''
import hashlib,json,os,pathlib,stat,sys,types
try:
 r=json.loads(RAW); s={}
 for key,row in r['seed'].items():
  fd=os.open(str(pathlib.Path(ROOT)/'seed'/(key+'.py')),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   st=os.fstat(fd)
   if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=0o600: raise ValueError()
   b=os.read(fd,65537)
   if len(b)!=row['bytes'] or hashlib.sha256(b).hexdigest()!=row['sha256']: raise ValueError()
   s[key]=b
  finally: os.close(fd)
 m=types.ModuleType('authenticated_metadata_worker'); m.__file__='/authenticated-metadata-seed/adapter.py'
 exec(compile(s['adapter'],m.__file__,'exec',dont_inherit=True),m.__dict__)
 m.remote_worker(RAW,s)
except BaseException:
 os._exit(1)
os._exit(0)
'''


class Job:
    def __init__(self, call):
        self.done = threading.Event()
        self.value, self.error = None, None
        def work():
            try:
                self.value = call()
            except BaseException:
                self.error = "bootstrap-job-failed"
            finally:
                self.done.set()
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()


def capture_identities(m, owner, connection, wrapped, probe_code, python):
    pid, uid, _ = m.peer(connection)
    require(uid == 0)
    worker = m.process(pid)
    helper = m.process(worker["ppid"])
    require(helper["ppid"] == owner["pid"] and helper["pgid"] == helper["sid"] == helper["pid"]
            and worker["pgid"] == worker["sid"] == helper["pid"]
            and owner["start_ticks"] <= helper["start_ticks"] <= worker["start_ticks"])
    args = m.command(helper["pid"])
    require(len(args) == 8 + len(wrapped) and args[:6] == [python, "-I", "-S", "-B", "-c", probe_code]
            and all(x.isascii() and x.isdecimal() and 3 <= int(x) < 2**31 for x in args[6:8])
            and args[6] != args[7] and args[8:] == wrapped)
    require(all(m.same_process(row) for row in (owner, helper, worker)))
    return worker, helper


def wait_connection(m, listener, deadline, job):
    listener.setblocking(False)
    while True:
        m.left(deadline)
        require(not job.done.is_set())
        if select.select([listener], [], [], min(.1, m.left(deadline)))[0]:
            return listener.accept()[0]


def remote_owner(raw_request, raw_consume, sources):
    m, request = request_checked(raw_request, sources)
    deadline = request["deadline_monotonic"]
    m.left(deadline)
    require(m.clock() == {key: request[key] for key in ("boot_id", "clock_namespace")})
    consume = m.parsed(raw_consume)
    require(set(consume) == {"schema", "request_sha256", "admission_sha256", "transport_sha256",
        "seed", "attempt_id", "local_deadline_monotonic", "deadline_monotonic"}
        and consume["schema"] == "private.metadata-bootstrap-consume/v2"
        and consume["request_sha256"] == sha(raw_request) and consume["seed"] == request["seed"]
        and consume["attempt_id"] == request["attempt_id"]
        and consume["deadline_monotonic"] == deadline)
    for key in ("admission_sha256", "transport_sha256"):
        m.digest(consume[key])
    owner = m.process(os.getpid())
    cache = load(sources["cache"], "cache")
    inspection = load(sources["inspection"], "inspection")
    resource = load(sources["resource"], "resource")
    probe = load(sources["probe"], "probe")
    root = control_root(request)
    root_id = m.claim(root.parent, root.name, request["parent_identity"], deadline)
    m.publish(root, "consume.json", raw_consume, deadline, root_id)
    m.publish(root, "owner.json", encoded(owner), deadline, root_id)
    seed_id = m.claim(root, "seed", root_id, deadline)
    for name in sorted(SEED_KEYS):
        m.publish(root / "seed", name + ".py", sources[name], deadline, seed_id)
    inputs_id = m.claim(root, "inputs", root_id, deadline)
    ownership = inspection.Ownership(cache, root / "ownership", request["attempt_id"], PRODUCER,
                                     request["seed"]["adapter"]["sha256"])
    m.left(deadline)
    socket_path = root / "installer.sock"
    # Linux sockaddr_un has a short fixed path buffer; fail before binding.
    require(len(os.fsencode(socket_path)) < 108)
    state = {"limits_read_back": False, "empty_verified": False, "group_removed": False,
             "oom_kill": None, "memory_peak_bytes": None, "populated_before_cleanup": None,
             "limits": dict(resource.LIMITS)}
    job = None
    connection = None
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    cancelled = False
    def cancel(*_):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise InterruptedError("bootstrap-cancelled")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        m.left(deadline)
        listener.bind(str(socket_path))
        listener.listen(1)
        with resource.cgroup(state) as (group_fd, group_name):
            info = os.fstat(group_fd)
            ownership.group(group_name, info, resource.LIMITS)
            group_raw = m.read_record(root / "ownership", "group.json")
            group = m.parsed(group_raw, ownership=True)["group"]
            worker_argv = [request["python"], "-I", "-S", "-B", "-c", worker_program(root, raw_request)]
            wrapped = [request["python"], "-I", "-S", "-B", "-c", inspection.RESOURCE_BOOTSTRAP,
                group_name, str(info.st_dev), str(info.st_ino), json.dumps(resource.LIMITS, sort_keys=True), *worker_argv]
            # Leave the pinned helper's existing 10s cleanup plus capture's 1s
            # reap inside the SAME cutoff, not a fresh per-stage budget.
            work_deadline = deadline - 11
            m.left(work_deadline)
            job = Job(lambda: probe.capture(wrapped, timeout=m.left(work_deadline), limit=4096))
            try:
                connection = wait_connection(m, listener, work_deadline, job)
                require(m.packet(connection, work_deadline) == b"READY")
                worker, helper = capture_identities(m, owner, connection, wrapped, probe.SUPERVISOR, request["python"])
                require(m.command(worker["pid"]) == worker_argv
                        and worker in m.group_read(group, members=True)["processes"])
                record = {"schema": "private.metadata-bootstrap-workers/v2", "request_sha256": sha(raw_request),
                    "owner": owner, "helper": helper, "worker": worker, "group_sha256": sha(group_raw)}
                worker_raw = encoded(record)
                m.publish(root, "worker.json", worker_raw, deadline, root_id)
                context_raw = encoded(m.worker_context(request, root, raw_consume, worker_raw, group, inputs_id))
                m.publish(root, "context.json", context_raw, deadline, root_id)
                m.packet(connection, work_deadline, context_raw)
                require(m.packet(connection, work_deadline) == b"CONFINED:" + sha(context_raw).encode("ascii"))
                m.frame(1, work_deadline, encoded({"kind": "confined", "request_sha256": sha(raw_request),
                    "context_sha256": sha(context_raw), "worker_sha256": sha(worker_raw)}))
                capsule_bytes = m.frame(0, work_deadline)
                require(sha(capsule_bytes) == request["capsule_sha256"])
                m.eof(0, work_deadline)
                m.packet(connection, work_deadline, capsule_bytes)
                while not job.done.wait(min(.1, m.left(work_deadline))):
                    pass
                require(job.error is None and not cancelled)
                ack = m.parsed(job.value)
                expected = {"schema": "private.metadata-bootstrap-worker-ack/v2", "request_sha256": sha(raw_request),
                    "context_sha256": sha(context_raw), "prepared_sha256": request["prepared_sha256"],
                    "attempt_id": request["attempt_id"], "producer": PRODUCER,
                    "deadline_monotonic": deadline, **m.FLAGS}
                require(set(ack) == set(expected) | {"installation_sha256", "layout_sha256"}
                        and all(type(ack[k]) is type(v) and ack[k] == v for k, v in expected.items()))
                for key in ("installation_sha256", "layout_sha256"):
                    m.digest(ack[key])
                installed = root / "inputs" / request["attempt_id"]
                require(sha(m.read_record(installed, "installation.json")) == ack["installation_sha256"]
                        and sha(m.read_record(installed, "bootstrap-layout.json")) == ack["layout_sha256"])
            finally:
                if connection is not None:
                    connection.close()
                if job is not None and not job.done.is_set():
                    resource.write(group_fd, "cgroup.kill", "1")
                    job.done.wait(max(0, deadline - time.monotonic()))
        require(job is not None and job.done.is_set() and job.error is None and not cancelled
                and all(state[k] is True for k in ("limits_read_back", "empty_verified", "group_removed"))
                and state["oom_kill"] == 0)
        m.left(deadline)
        terminal = {"schema": "private.metadata-bootstrap-terminal/v2", "request_sha256": sha(raw_request),
            "worker_ack": ack, "resources": state, "worker_exit_zero": True,
            "owned_absence_confirmed": False, **m.FLAGS}
        terminal_raw = encoded(terminal)
        m.publish(root, "terminal.json", terminal_raw, deadline, root_id)
    finally:
        listener.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    m.left(deadline)
    m.frame(1, deadline, encoded({"kind": "terminal", "request_sha256": sha(raw_request),
                                "terminal_sha256": sha(terminal_raw)}))
    m.left(deadline)


def prepare_layout(m, request, root, installed, observation, lease):
    deadline = request["deadline_monotonic"]
    identity = m.parsed(observation)["directory_identity"]
    lease.check(lease.raw, deadline)
    mount_id = m.claim(installed, "destination-mount", identity, deadline)
    lease.check(lease.raw, deadline)
    m.publish(installed, "launch.lock", b"", deadline, identity)
    with m.directory(installed) as parent:
        require(m.directory_id(os.fstat(parent)) == identity)
        lock_id = m.file_id(os.stat("launch.lock", dir_fd=parent, follow_symlinks=False))
    with m.directory(installed / "destination-mount") as mount:
        require(m.directory_id(os.fstat(mount)) == mount_id and not os.listdir(mount))
    # A freshly created directory is not permission to mount; reject unexpected
    # attachments visible in this namespace without invoking mount/findmnt.
    encoded_target = os.fsencode(installed / "destination-mount")
    require(all(line.split()[4] != encoded_target
                for line in m.kernel_read("/proc/self/mountinfo", 1 << 20).splitlines()))
    value = {"schema": "private.metadata-bootstrap-layout/v2", "producer": PRODUCER,
        "attempt_id": request["attempt_id"], "work_request_sha256": request["work_request_sha256"],
        "request_sha256": sha(encoded(request)), "context_sha256": sha(lease.raw),
        "capsule_sha256": request["capsule_sha256"], "prepared_sha256": request["prepared_sha256"],
        "installation_sha256": sha(observation), "installed_directory": identity,
        "mount_directory": mount_id, "launch_lock": lock_id, **m.FLAGS}
    raw = encoded(value)
    lease.check(lease.raw, deadline)
    m.publish(installed, "bootstrap-layout.json", raw, deadline, identity)
    lease.check(lease.raw, deadline)
    return raw


def remote_worker(raw_request, sources):
    m, request = request_checked(raw_request, sources)
    root, deadline = control_root(request), request["deadline_monotonic"]
    c = load(sources["capsule"], "capsule")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    lease = None
    try:
        m.left(deadline)
        connection.settimeout(m.left(deadline))
        connection.connect(str(root / "installer.sock"))
        owner = m.parsed(m.read_record(root, "owner.json"))
        require(m.peer(connection)[:2] == (owner["pid"], 0) and m.same_process(owner))
        m.packet(connection, deadline, b"READY")
        context_raw = m.packet(connection, deadline)
        lease = m.KernelLease(request, root, context_raw)
        m.packet(connection, deadline, b"CONFINED:" + sha(context_raw).encode("ascii"))
        capsule_bytes = m.packet(connection, deadline)
        lease.check(context_raw, deadline)
        installed = root / "inputs" / request["attempt_id"]
        observation = c.install(capsule_bytes, str(installed), context_raw, sha(context_raw),
                                deadline=deadline, supervisor_check=lease.check)
        layout = prepare_layout(m, request, root, installed, observation, lease)
        ack = {"schema": "private.metadata-bootstrap-worker-ack/v2", "request_sha256": sha(raw_request),
            "context_sha256": sha(context_raw), "prepared_sha256": request["prepared_sha256"],
            "attempt_id": request["attempt_id"], "producer": PRODUCER, "deadline_monotonic": deadline,
            "installation_sha256": sha(observation), "layout_sha256": sha(layout), **m.FLAGS}
    finally:
        try:
            connection.close()
        finally:
            if lease is not None:
                lease.close()
    m.left(deadline)
    raw = encoded(ack)
    require(len(raw) <= 4096)
    m.pipe_bytes(1, deadline, raw=raw)
    m.left(deadline)


def transport_checked(m, raw, expected):
    require(sha(raw) == expected)
    value = m.parsed(raw)
    require(set(value) == {"schema", "argv", "executable_sha256", "remote_python"}
            and value["schema"] == "nib.pinned-posix-command-transport/v1")
    argv = value["argv"]
    require(type(argv) is list and 2 <= len(argv) <= 128 and argv[-1] == "{command}"
            and all(type(a) is str and a and "\0" not in a for a in argv)
            and "{command}" not in argv[:-1])
    path = Path(argv[0])
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid())
                and not info.st_mode & 0o022 and info.st_mode & 0o111 and 0 < info.st_size <= 16 << 20)
        content = os.read(fd, (16 << 20) + 1)
        stable = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
                             st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        require(len(content) == info.st_size and sha(content) == value["executable_sha256"]
                and stable(os.fstat(fd)) == stable(info)
                == stable(os.stat(path, follow_symlinks=False)))
    finally:
        os.close(fd)
    return value


def command_line(channel, program):
    command = shlex.join([channel["remote_python"], "-I", "-S", "-B", "-c", program])
    require(len(command.encode("utf-8")) <= 65536)
    return channel["argv"][:-1] + [command]


def observer_program(request, sources, known, action):
    require(action in ("snapshot", "cancel"))
    raw = sources["monitor"]
    inventory = encoded(known)
    require(len(inventory) <= 65536)
    program = "import base64,hashlib,os,types\ntry:\n"
    body = "b=base64.b64decode(" + repr(base64.b64encode(raw)) + ",validate=True)\n"
    body += "assert hashlib.sha256(b).hexdigest()==" + repr(sha(raw)) + "\n"
    body += "m=types.ModuleType('authenticated_bootstrap_monitor'); exec(compile(b,'<bootstrap-monitor>','exec'),m.__dict__)\n"
    body += "d=__import__('time').monotonic()+20\n"
    body += "b=m.pipe_bytes(0,d,count=" + str(len(inventory)) + ")\n"
    body += "m.require(m.sha(b)==" + repr(sha(inventory)) + "); k=m.parsed(b)\n"
    if action == "cancel":
        # The resource helper is raw, bounded, hash-checked input, not part of
        # the shell argument or compressed executable data.
        body += "b=m.pipe_bytes(0,d,count=" + str(PUBLIC["resource"][1]) + ")\n"
        body += "m.require(m.sha(b)==" + repr(PUBLIC["resource"][2]) + ")\n"
        body += "h=types.ModuleType('authenticated_bootstrap_resource'); exec(compile(b,'<resource>','exec'),h.__dict__)\n"
    body += "m.eof(0,d)\n"
    body += "r=m." + action + "(" + repr(request) + ",k" + (",h" if action == "cancel" else "") + ")\n"
    body += "m.frame(1,d,m.encoded(r))\n"
    program += "".join(" " + line + "\n" for line in body.splitlines())
    program += "except BaseException:\n os._exit(1)\nos._exit(0)\n"
    return program


def read_exit(m, process, deadline):
    with m.nonblocking(process.stdout.fileno()):
        while True:
            require(select.select([process.stdout], [], [], m.left(deadline))[0])
            m.left(deadline)
            try:
                require(os.read(process.stdout.fileno(), 1) == b"")
                break
            except BlockingIOError:
                continue
    require(process.wait(timeout=m.left(deadline)) == 0)
    m.left(deadline)


def observe_call(m, channel, runner, request, sources, known, action):
    # Observation/cancellation remains bounded after the work cutoff, and can
    # never authorize additional work or re-arm the work deadline.
    deadline = time.monotonic() + 20
    program = observer_program(request, sources, known, action)
    process = subprocess.Popen(command_line(channel, program), env=ENV, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        m.pipe_bytes(process.stdin.fileno(), deadline, raw=encoded(known))
        if action == "cancel":
            m.pipe_bytes(process.stdin.fileno(), deadline, raw=sources["resource"])
        process.stdin.close()
        result = m.parsed(m.frame(process.stdout.fileno(), deadline))
        read_exit(m, process, deadline)
    finally:
        try:
            if not process.stdin.closed:
                process.stdin.close()
        finally:
            try:
                runner.cleanup_process_group(process)
            finally:
                process.stdout.close()
    m.left(deadline)
    return result


def admitted(raw_request, raw_admission, admission_pin, raw_transport, sources, runner_raw, deadline):
    # No file, transport or capsule action precedes the external admission pin.
    require(admission_pin is not None and type(raw_admission) is bytes and len(raw_admission) <= 65536
            and type(admission_pin) is str and sha(raw_admission) == admission_pin)
    # Minimal parsing before loading independently approved private code.
    value = json.loads(raw_admission)
    require(encoded(value) == raw_admission and type(value) is dict and set(value) == {
        "schema", "request_sha256", "transport_sha256", "seed", "local_deadline_monotonic",
        "operator_authorized", "target_accounting_reviewed", "no_overlap"}
        and value["schema"] == "private.metadata-bootstrap-admission/v2"
        and all(value[k] is True for k in ("operator_authorized", "target_accounting_reviewed", "no_overlap"))
        and value["request_sha256"] == sha(raw_request)
        and type(value["local_deadline_monotonic"]) is type(deadline)
        and value["local_deadline_monotonic"] == deadline)
    validate_sources(sources, value["seed"])
    m, request = request_checked(raw_request, sources)
    require(request["seed"] == value["seed"] and sha(runner_raw) == RUNNER_SHA)
    channel = transport_checked(m, raw_transport, value["transport_sha256"])
    require(channel["remote_python"] == request["python"])
    m.left(deadline)
    return m, request, channel, load(runner_raw, "runner")


class Retained:
    """Small in-memory inventory; no filesystem work under the lock."""
    def __init__(self):
        self.lock = threading.Lock()
        self.value = {"inventory_complete": False, "processes": [], "records": {}}
        self.abort = threading.Event()
        self.uncertain = False
        self.cutoff = math.inf

    def get(self):
        with self.lock:
            return json.loads(encoded(self.value))

    def update(self, value):
        with self.lock:
            merged = json.loads(encoded(value))
            for row in self.value["processes"]:
                if row not in merged["processes"]:
                    merged["processes"].append(row)
            for name, raw in self.value["records"].items():
                require(name not in merged["records"] or merged["records"][name] == raw)
                merged["records"][name] = raw
            if "root_identity" in self.value:
                require(merged.get("root_identity", self.value["root_identity"]) == self.value["root_identity"])
                merged["root_identity"] = self.value["root_identity"]
            merged["inventory_complete"] = (merged["inventory_complete"] is True
                                             or self.value["inventory_complete"] is True)
            self.value = merged


class Journal:
    """A stalled journal cannot stall cancellation or the observation scheduler."""
    def __init__(self, m, root, identity):
        self.queue = queue.Queue(64)
        self.failed = threading.Event()
        self.pending_since = None
        self.count = 0
        def write():
            while True:
                item = self.queue.get()
                try:
                    if item is None:
                        return
                    number, raw = item
                    m.publish(root, "event-%06d.json" % number, raw,
                              time.monotonic() + 20, identity)
                except BaseException:
                    self.failed.set()
                finally:
                    self.queue.task_done()
        self.thread = threading.Thread(target=write, daemon=True)
        self.thread.start()

    def emit(self, value):
        try:
            raw = encoded(value)
            require(len(raw) <= 65536)
            self.count += 1
            self.queue.put_nowait((self.count, raw))
            if self.pending_since is None:
                self.pending_since = time.monotonic()
        except BaseException:
            self.failed.set()

    def healthy(self):
        if self.queue.unfinished_tasks == 0:
            self.pending_since = None
        elif self.pending_since is not None and time.monotonic() - self.pending_since >= 5:
            self.failed.set()
        return not self.failed.is_set()

    def close(self):
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            self.failed.set()


def complete(m, c, request, observed, terminal):
    """Terminal bytes, successful remote exit and fresh absence are ALL required."""
    require(observed["owned_absence_confirmed"] is True and not observed["errors"])
    records = observed["records"]
    raw = records["terminal.json"].encode("ascii")
    require(terminal["terminal_sha256"] == sha(raw))
    value = m.parsed(raw)
    expected = {"schema": "private.metadata-bootstrap-terminal/v2", "request_sha256": sha(encoded(request)),
                "worker_exit_zero": True, "owned_absence_confirmed": False, **m.FLAGS}
    require(set(value) == set(expected) | {"worker_ack", "resources"}
            and all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()))
    resources = value["resources"]
    require(set(resources) == {"limits", "limits_read_back", "empty_verified", "group_removed",
                               "oom_kill", "memory_peak_bytes", "populated_before_cleanup"}
            and resources["limits"] == m.LIMITS
            and all(resources[k] is True for k in ("limits_read_back", "empty_verified", "group_removed"))
            and type(resources["oom_kill"]) is int and resources["oom_kill"] == 0
            and type(resources["memory_peak_bytes"]) is int and resources["memory_peak_bytes"] >= 0
            and type(resources["populated_before_cleanup"]) is int
            and resources["populated_before_cleanup"] in (0, 1))
    ack = value["worker_ack"]
    expected = {"schema": "private.metadata-bootstrap-worker-ack/v2", "request_sha256": sha(encoded(request)),
        "context_sha256": sha(records["context.json"].encode()), "prepared_sha256": request["prepared_sha256"],
        "attempt_id": request["attempt_id"], "producer": PRODUCER,
        "deadline_monotonic": request["deadline_monotonic"], **m.FLAGS}
    require(set(ack) == set(expected) | {"installation_sha256", "layout_sha256"}
            and all(type(ack[k]) is type(v) and ack[k] == v for k, v in expected.items()))
    prepared = c.prepared(records["prepared.json"].encode(), request["prepared_sha256"])
    require(prepared["attempt_id"] == request["attempt_id"]
            and prepared["request_sha256"] == request["work_request_sha256"])
    installation = m.parsed(records["installation.json"].encode())
    layout = m.parsed(records["bootstrap-layout.json"].encode())
    require(set(installation) == {"schema", "kind", "producer", "attempt_id", "context_sha256",
        "capsule_sha256", "prepared_sha256", "intent_sha256", "deadline_monotonic", "directory_identity",
        "files", "file_identities", "reopened_verified", *c.FALSE_FLAGS}
        and installation["kind"] == "local-file-observation"
        and set(installation["file_identities"]) == set(c.PINS)
        and set(layout) == {"schema", "producer", "attempt_id", "work_request_sha256", "request_sha256",
            "context_sha256", "capsule_sha256", "prepared_sha256", "installation_sha256",
            "installed_directory", "mount_directory", "launch_lock", *m.FLAGS})
    require(sha(records["installation.json"].encode()) == ack["installation_sha256"]
            and sha(records["bootstrap-layout.json"].encode()) == ack["layout_sha256"]
            and installation["schema"] == "private.metadata-source-install-observation/v1"
            and installation["reopened_verified"] is True and installation["files"] == c.rows()
            and all(installation[k] is False for k in c.FALSE_FLAGS)
            and layout["schema"] == "private.metadata-bootstrap-layout/v2"
            and layout["request_sha256"] == sha(encoded(request))
            and layout["work_request_sha256"] == request["work_request_sha256"]
            and layout["installation_sha256"] == ack["installation_sha256"]
            and layout["installed_directory"] == installation["directory_identity"]
            and all(layout[k] is False for k in m.FLAGS))
    for key, expected in (("producer", PRODUCER), ("attempt_id", request["attempt_id"]),
                          ("context_sha256", ack["context_sha256"]),
                          ("capsule_sha256", request["capsule_sha256"]),
                          ("prepared_sha256", request["prepared_sha256"])):
        require(installation[key] == layout[key] == expected)
    require(installation["deadline_monotonic"] == request["deadline_monotonic"])
    return value


def watch(m, channel, runner, request, sources, retained, transfer, journal, deadline, handoff):
    """15s observations; deadline cancellation never waits for dispatch/journaling.

    Unknown remote observer exits permanently prevent absence claims in this
    invocation. A joined human handoff can end this loop, never authorize work.
    """
    observing = cancelling = handing = None
    uncertain_cancel_attempted = False
    last = None
    next_observe = next_cancel = time.monotonic()
    while True:
        now = time.monotonic()
        cutoff = min(deadline, retained.cutoff)
        if now >= cutoff or not journal.healthy() or transfer.error:
            retained.abort.set()
        if observing is not None and observing.done.is_set():
            if observing.error:
                retained.uncertain = True
                retained.abort.set()
                last = None
            else:
                last = observing.value
                known = retained.get()
                for row in last["processes"]:
                    if row not in known["processes"]:
                        known["processes"].append(row)
                retained.update(known)
                if last["errors"]:
                    retained.abort.set()
                journal.emit({"kind": "observation", "snapshot": last})
            observing = None
        if cancelling is not None and cancelling.done.is_set():
            if cancelling.error:
                retained.uncertain = True
            journal.emit({"kind": "cancellation", "exit_observed": cancelling.error is None})
            cancelling = None
        if (transfer.done.is_set() and last is not None and not retained.uncertain
                and last["owned_absence_confirmed"] is True and observing is None and cancelling is None):
            passed = False
            terminal = None
            if not retained.abort.is_set() and transfer.error is None:
                try:
                    terminal = complete(m, load(sources["capsule"], "capsule"), request, last, transfer.value)
                    passed = journal.healthy() and time.monotonic() < cutoff
                except Exception:
                    retained.abort.set()
            # Wait for durable observations without blocking the deadline loop.
            if journal.queue.unfinished_tasks == 0 or not journal.healthy():
                return {"bootstrap_complete": passed, "owned_absence_confirmed": True,
                        "known": retained.get(), "terminal": terminal, **m.FLAGS}
        if (observing is None and now >= next_observe and not retained.uncertain
                and (retained.get()["processes"] or retained.abort.is_set())):
            known = retained.get()
            observing = Job(lambda known=known: observe_call(m, channel, runner, request, sources, known, "snapshot"))
            next_observe = now + 15
        if (retained.abort.is_set() and cancelling is None and now >= next_cancel
                and (not retained.uncertain or not uncertain_cancel_attempted) and retained.get()["processes"]):
            known = retained.get()
            if retained.uncertain:
                uncertain_cancel_attempted = True
            cancelling = Job(lambda known=known: observe_call(m, channel, runner, request, sources, known, "cancel"))
            next_cancel = now + 15
        if handoff is not None:
            snapshot = encoded({"schema": "private.metadata-bootstrap-handoff/v2",
                "request_sha256": sha(encoded(request)), "known": retained.get(),
                "remote_exit_uncertain": retained.uncertain, "owned_absence_confirmed": False,
                "observation_pending": observing is not None, "cancellation_pending": cancelling is not None,
                "transfer_pending": not transfer.done.is_set(), "requires_continuing_observation": True})
            if handing is not None and handing.done.is_set():
                ack, offered = handing.value if handing.error is None else (None, None)
                if (type(ack) is dict and ack.get("monitoring_responsibility_accepted") is True
                        and ack == {"request_sha256": sha(encoded(request)), "snapshot_sha256": sha(snapshot),
                                    "monitoring_responsibility_accepted": True} and offered == snapshot):
                    retained.abort.set()
                    return {"bootstrap_complete": False, "owned_absence_confirmed": False,
                            "handoff_ack": ack, "known": retained.get(), **m.FLAGS}
                handing = None
            if retained.abort.is_set() and handing is None:
                handing = Job(lambda snapshot=snapshot: (handoff(snapshot), snapshot))
        time.sleep(.05)


def run(raw_request, raw_admission, raw_transport, sources, runner_raw, capsule_bytes,
        local_parent, *, deadline, admission_pin=OPERATIONAL_ADMISSION_PIN, handoff=None):
    """One admitted attempt. Failure stays monitored until absence or exact handoff.

    The caller must authenticate this module before invoking it. No live default,
    CLI, transport fallback, automatic replay or metadata launch exists.
    """
    m, request, channel, runner = admitted(raw_request, raw_admission, admission_pin,
        raw_transport, sources, runner_raw, deadline)
    require(threading.current_thread() is threading.main_thread())
    require(type(capsule_bytes) is bytes and 0 < len(capsule_bytes) <= 65536
            and sha(capsule_bytes) == request["capsule_sha256"])
    local_parent = Path(local_parent)
    with m.directory(local_parent) as fd:
        parent_id = m.directory_id(os.fstat(fd))
    local = local_parent / request["attempt_id"]
    identity = m.claim(local_parent, local.name, parent_id, deadline)
    consume = encoded({"schema": "private.metadata-bootstrap-consume/v2", "request_sha256": sha(raw_request),
        "admission_sha256": admission_pin, "transport_sha256": sha(raw_transport), "seed": request["seed"],
        "attempt_id": request["attempt_id"], "local_deadline_monotonic": deadline,
        "deadline_monotonic": request["deadline_monotonic"]})
    m.publish(local, "consume.json", consume, deadline, identity)
    program = seed_program(raw_request, consume)
    argv = command_line(channel, program)  # Exact final quoting/envelope, before dispatch.
    retained = Retained()
    journal = Journal(m, local, identity)
    def transfer():
        nonlocal deadline
        process = None
        def guard():
            m.left(deadline)
            require(not retained.abort.is_set())
        try:
            guard()
            dispatch_started = time.monotonic()
            process = subprocess.Popen(argv, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
            hello = m.parsed(m.frame(process.stdout.fileno(), deadline))
            require(set(hello) == {"kind", "request_sha256", "owner", "monotonic"} and hello["kind"] == "seed-owner"
                and hello["request_sha256"] == sha(raw_request))
            owner = hello["owner"]
            require({key: owner[key] for key in ("boot_id", "clock_namespace")}
                == {key: request[key] for key in ("boot_id", "clock_namespace")})
            require(type(hello["monotonic"]) in (int, float) and math.isfinite(hello["monotonic"])
                    and 0 < hello["monotonic"] < request["deadline_monotonic"])
            # Conservative translation, not a reset: dispatch began before the
            # bound remote sample. Network/setup elapsed time is charged twice.
            deadline = min(deadline, dispatch_started + request["deadline_monotonic"] - hello["monotonic"])
            retained.cutoff = deadline
            known = retained.get()
            known["processes"] = [owner]
            retained.update(known)
            m.publish(local, "owner.json", encoded(hello), deadline, identity)
            m.publish(local, "clock-binding.json", encoded({"dispatch_started": dispatch_started,
                "remote_monotonic": hello["monotonic"], "local_cutoff": deadline,
                "remote_cutoff": request["deadline_monotonic"], "request_sha256": sha(raw_request)}), deadline, identity)
            guard()
            for key in sorted(SEED_KEYS):
                m.pipe_bytes(process.stdin.fileno(), deadline, raw=sources[key])
            ready = m.parsed(m.frame(process.stdout.fileno(), deadline))
            require(ready.get("kind") == "confined" and ready.get("request_sha256") == sha(raw_request))
            try:
                observed = observe_call(m, channel, runner, request, sources, known, "snapshot")
            except BaseException:
                retained.uncertain = True
                raise
            require(not observed["errors"] and observed["group"]["present"] is True)
            records = observed["records"]
            require(sha(records["context.json"].encode()) == ready["context_sha256"]
                and sha(records["worker.json"].encode()) == ready["worker_sha256"]
                and records["consume.json"].encode() == consume)
            workers = m.parsed(records["worker.json"].encode())
            require(workers["owner"] == owner and all(workers[k] in observed["processes"] for k in ("owner", "helper", "worker")))
            known = {"inventory_complete": True, "records": records, "processes": observed["processes"],
                     "root_identity": observed["root_identity"]}
            retained.update(known)
            m.publish(local, "inventory.json", encoded(known), deadline, identity)
            guard()
            # No capsule bytes crossed before this independently read, durably
            # retained owner/helper/worker/group inventory.
            m.frame(process.stdin.fileno(), deadline, capsule_bytes)
            process.stdin.close()
            terminal = m.parsed(m.frame(process.stdout.fileno(), deadline))
            require(set(terminal) == {"kind", "request_sha256", "terminal_sha256"}
                and terminal["kind"] == "terminal" and terminal["request_sha256"] == sha(raw_request))
            read_exit(m, process, deadline)
        finally:
            if process is not None:
                try:
                    try:
                        if not process.stdin.closed:
                            process.stdin.close()
                    finally:
                        try:
                            runner.cleanup_process_group(process)
                        finally:
                            process.stdout.close()
                except BaseException:
                    retained.uncertain = True
                    raise
        guard()
        return terminal
    handlers = {sig: signal.signal(sig, lambda *_: retained.abort.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    job = Job(transfer)
    try:
        result = watch(m, channel, runner, request, sources, retained, job, journal, deadline, handoff)
    finally:
        retained.abort.set()
        journal.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    if result["bootstrap_complete"]:
        m.left(min(deadline, retained.cutoff))
    return result


if __name__ == "__main__":
    raise SystemExit("No bootstrap CLI. Independent admission and supervised integration required.")

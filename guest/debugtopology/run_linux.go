//go:build linux && amd64

// SPDX-License-Identifier: Apache-2.0
package debugtopology

import (
	"context"
	"encoding/binary"
	"net"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"
)

const operationTimeout = time.Second

type routeSocket struct {
	fd        int
	port, seq uint32
	ctx       context.Context
}

func openRoute(ctx context.Context) (*routeSocket, error) {
	fd, err := syscall.Socket(syscall.AF_NETLINK, syscall.SOCK_RAW|syscall.SOCK_CLOEXEC, syscall.NETLINK_ROUTE)
	if err != nil {
		return nil, errRejected
	}
	fail := func() (*routeSocket, error) { syscall.Close(fd); return nil, errRejected }
	if syscall.Bind(fd, &syscall.SockaddrNetlink{Family: syscall.AF_NETLINK}) != nil {
		return fail()
	}
	addr, err := syscall.Getsockname(fd)
	a, ok := addr.(*syscall.SockaddrNetlink)
	if err != nil || !ok || a.Pid == 0 || a.Groups != 0 {
		return fail()
	}
	return &routeSocket{fd: fd, port: a.Pid, ctx: ctx}, nil
}

func (s *routeSocket) transaction(kind, flags, expected uint16, payload []byte) ([][]byte, error) {
	deadline, ok := s.ctx.Deadline()
	if !ok || s.ctx.Err() != nil || s.seq >= 64 {
		return nil, errRejected
	}
	deadline = minTime(deadline, time.Now().Add(operationTimeout))
	s.seq++
	req := message(kind, flags, s.seq, payload)
	timeout := syscall.NsecToTimeval(time.Until(deadline).Nanoseconds())
	if time.Until(deadline) <= 0 || syscall.SetsockoptTimeval(s.fd, syscall.SOL_SOCKET, syscall.SO_SNDTIMEO, &timeout) != nil ||
		syscall.Sendto(s.fd, req, 0, &syscall.SockaddrNetlink{Family: syscall.AF_NETLINK}) != nil {
		return nil, errRejected
	}
	r := replies{request: req, port: s.port, dump: flags == dumpFlag, expected: expected}
	for packets := 0; packets < 8 && !r.done; packets++ {
		if s.ctx.Err() != nil || !time.Now().Before(deadline) {
			return nil, errRejected
		}
		timeout = syscall.NsecToTimeval(time.Until(deadline).Nanoseconds())
		if syscall.SetsockoptTimeval(s.fd, syscall.SOL_SOCKET, syscall.SO_RCVTIMEO, &timeout) != nil {
			return nil, errRejected
		}
		buffer := make([]byte, 32768)
		n, _, recvFlags, addr, err := syscall.Recvmsg(s.fd, buffer, nil, 0)
		from, ok := addr.(*syscall.SockaddrNetlink)
		if err != nil || !ok || from.Pid != 0 || from.Groups != 0 || recvFlags != 0 || r.accept(buffer[:n]) != nil {
			return nil, errRejected
		}
	}
	if !r.done || s.ctx.Err() != nil || !time.Now().Before(deadline) {
		return nil, errRejected
	}
	return r.data, nil
}

func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}

func (s *routeSocket) links() ([]link, error) {
	raw, err := s.transaction(getLink, dumpFlag, newLink, linkInfo(0))
	if err != nil || len(raw) == 0 || len(raw) > 2 {
		return nil, errRejected
	}
	var out []link
	for _, b := range raw {
		l, err := parseLink(b)
		if err != nil {
			return nil, err
		}
		for _, old := range out {
			if old.index == l.index || old.name == l.name {
				return nil, errRejected
			}
		}
		out = append(out, l)
	}
	return out, nil
}

func (s *routeSocket) noAddressesOrRoutes() error {
	// Check both IP families explicitly. AF_UNSPEC route dumps can include other
	// kernel protocol families; none of those is an IP reachability claim here.
	for _, family := range []byte{syscall.AF_INET, syscall.AF_INET6} {
		for _, op := range []struct {
			kind, reply uint16
			size        int
		}{{getAddr, 20, 8}, {getRoute, 24, 12}} {
			body := make([]byte, op.size)
			body[0] = family
			raw, err := s.transaction(op.kind, dumpFlag, op.reply, body)
			if err != nil || len(raw) != 0 {
				return errRejected
			}
		}
	}
	return nil
}

func (s *routeSocket) empty() error {
	links, err := s.links()
	if err != nil || len(links) != 1 || !loopback(links[0], true) {
		return errRejected
	}
	return s.noAddressesOrRoutes()
}

// Namespace magic links are read only for ourselves or the child's parent
// guard. Mutation targets arrive by SCM_RIGHTS, never by a reusable child PID.
// Linux v6.8 include/uapi/linux/nsfs.h: _IO(0xb7, 3), CLONE_NEWNET.
func openNamespace(pid int) (*os.File, namespace, error) {
	f, err := os.Open("/proc/" + strconv.Itoa(pid) + "/ns/net")
	if err != nil {
		return nil, namespace{}, errRejected
	}
	id, err := namespaceOf(f)
	if err != nil {
		f.Close()
		return nil, namespace{}, errRejected
	}
	return f, id, nil
}

func namespaceOf(f *os.File) (namespace, error) {
	var st syscall.Stat_t
	typ, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), 0xb703, 0)
	if errno != 0 || typ != syscall.CLONE_NEWNET || syscall.Fstat(int(f.Fd()), &st) != nil || st.Ino == 0 {
		return namespace{}, errRejected
	}
	return namespace{st.Dev, st.Ino}, nil
}

func receiveNamespace(conn *net.UnixConn) (*os.File, namespace, error) {
	data, control := make([]byte, 32), make([]byte, syscall.CmsgSpace(16))
	n, oob, flags, _, readErr := conn.ReadMsgUnix(data, control)
	messages, parseErr := syscall.ParseSocketControlMessage(control[:oob])
	var received []int
	valid := true
	for _, msg := range messages {
		fds, err := syscall.ParseUnixRights(&msg)
		if err != nil {
			valid = false
		} else {
			received = append(received, fds...)
		}
	}
	keep := -1
	defer func() {
		for _, fd := range received {
			if fd != keep {
				syscall.Close(fd)
			}
		}
	}()
	if readErr != nil || parseErr != nil || !valid || len(messages) != 1 || len(received) != 1 ||
		n != 1 || data[0] != 'R' || flags & ^syscall.MSG_CMSG_CLOEXEC != 0 {
		return nil, namespace{}, errRejected
	}
	f := os.NewFile(uintptr(received[0]), "owned-namespace")
	id, err := namespaceOf(f)
	// Ownership transfers even on invalid descriptor type so f is closed once.
	keep = received[0]
	if err != nil {
		f.Close()
		return nil, namespace{}, errRejected
	}
	return f, id, nil
}

func channel(file *os.File, deadline time.Time) (*net.UnixConn, error) {
	c, err := net.FileConn(file)
	if err != nil {
		return nil, errRejected
	}
	u, ok := c.(*net.UnixConn)
	if !ok || u.SetDeadline(deadline) != nil {
		c.Close()
		return nil, errRejected
	}
	return u, nil
}

func readPacket(c *net.UnixConn, size int) ([]byte, error) {
	b := make([]byte, 32)
	n, oob, flags, _, err := c.ReadMsgUnix(b, nil)
	if err != nil || n != size || oob != 0 || flags & ^syscall.MSG_CMSG_CLOEXEC != 0 {
		return nil, errRejected
	}
	return b[:n], nil
}

func writePacket(c *net.UnixConn, b []byte) error {
	n, err := c.Write(b)
	if err != nil || n != len(b) {
		return errRejected
	}
	return nil
}

type ownedChild struct {
	cmd   *exec.Cmd
	wait  chan error
	conn  *net.UnixConn
	ns    *os.File
	empty bool
}

func (c *ownedChild) exchange(command byte, size int) ([]byte, error) {
	if writePacket(c.conn, []byte{command}) != nil {
		return nil, errRejected
	}
	return readPacket(c.conn, size)
}

type controller struct {
	ctx      context.Context
	children [2]*ownedChild
	parent   namespace
	before   link
	route    *routeSocket
}

func (c *controller) initial() error {
	f, id, err := openNamespace(os.Getpid())
	if err != nil {
		return err
	}
	if f.Close() != nil {
		return errRejected
	}
	c.parent = id
	c.route, err = openRoute(c.ctx)
	if err != nil {
		return err
	}
	links, err := c.route.links()
	if err != nil || len(links) != 1 || !loopback(links[0], false) {
		return errRejected
	}
	c.before = links[0]
	return nil
}

func (c *controller) spawn(role byte) (namespace, error) {
	if role > 1 || c.children[role] != nil {
		return namespace{}, errRejected
	}
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_SEQPACKET|syscall.SOCK_CLOEXEC, 0)
	if err != nil {
		return namespace{}, errRejected
	}
	parent := os.NewFile(uintptr(fds[0]), "parent")
	child := os.NewFile(uintptr(fds[1]), "child")
	defer parent.Close()
	defer child.Close()
	deadline, ok := c.ctx.Deadline()
	if !ok {
		return namespace{}, errRejected
	}
	conn, err := channel(parent, deadline)
	if err != nil {
		return namespace{}, errRejected
	}
	o := &ownedChild{conn: conn}
	c.children[role] = o // Own cleanup before starting anything.
	o.cmd = exec.CommandContext(c.ctx, "/proc/self/exe", "--topology-child="+strconv.Itoa(int(role)))
	o.cmd.Env = []string{"LANG=C", "LC_ALL=C"}
	o.cmd.ExtraFiles = []*os.File{child}
	o.cmd.SysProcAttr = &syscall.SysProcAttr{Cloneflags: syscall.CLONE_NEWNET, Pdeathsig: syscall.SIGKILL}
	o.cmd.WaitDelay = 100 * time.Millisecond
	if o.cmd.Start() != nil {
		return namespace{}, errRejected
	}
	o.wait = make(chan error, 1)
	go func() { o.wait <- o.cmd.Wait() }()
	var id namespace
	o.ns, id, err = receiveNamespace(conn)
	if err != nil || id == c.parent {
		return namespace{}, errRejected
	}
	return id, nil
}

func (c *controller) create() error {
	payload, err := createPair(uint32(c.children[0].ns.Fd()), uint32(c.children[1].ns.Fd()))
	if err != nil {
		return err
	}
	_, err = c.route.transaction(newLink, ackFlag|createExclusive, 0, payload)
	return err
}

func (c *controller) inspect(role byte) (pairEnd, error) {
	raw, err := c.children[role].exchange('I', 8)
	if err != nil {
		return pairEnd{}, err
	}
	return pairEnd{binary.LittleEndian.Uint32(raw), binary.LittleEndian.Uint32(raw[4:])}, nil
}

func (c *controller) remove() error {
	raw, err := c.children[0].exchange('D', 1)
	if err != nil || raw[0] != 'D' {
		return errRejected
	}
	return nil
}

func (c *controller) empty(role byte) error {
	raw, err := c.children[role].exchange('E', 1)
	if err != nil || raw[0] != 'E' {
		return errRejected
	}
	c.children[role].empty = true
	return nil
}

func (c *controller) unchanged() error {
	f, id, err := openNamespace(os.Getpid())
	if err != nil {
		return err
	}
	if f.Close() != nil || id != c.parent {
		return errRejected
	}
	links, err := c.route.links()
	if err != nil || len(links) != 1 || links[0] != c.before {
		return errRejected
	}
	return nil
}

func (c *controller) cleanup() error {
	failed := false
	// One common cleanup deadline, never one fresh deadline per process. Children
	// execute no subprocesses. Process.Kill uses Go's owned process handle, not a
	// reused PID or a host process-group search.
	deadline := time.Now().Add(time.Second)
	for _, child := range c.children {
		if child == nil {
			continue
		}
		graceful := child.empty && c.ctx.Err() == nil
		if graceful {
			if child.conn.SetDeadline(minTime(deadline, time.Now().Add(100*time.Millisecond))) != nil {
				failed = true
			}
			raw, err := child.exchange('X', 1)
			if err != nil || raw[0] != 'X' {
				graceful = false
				failed = true
			}
		}
		if child.conn.Close() != nil {
			failed = true
		}
		if child.wait != nil {
			if graceful {
				select {
				case err := <-child.wait:
					if err != nil {
						failed = true
					}
					child.wait = nil
				case <-time.After(min(100*time.Millisecond, max(time.Until(deadline), time.Nanosecond))):
					failed = true
				}
			}
			if child.wait != nil {
				_ = child.cmd.Process.Kill()
			}
		}
		if child.wait != nil {
			select {
			case <-child.wait:
			case <-time.After(max(time.Until(deadline), time.Nanosecond)):
				failed = true
			}
		}
		if child.ns != nil && child.ns.Close() != nil {
			failed = true
		}
	}
	if c.route != nil && syscall.Close(c.route.fd) != nil {
		failed = true
	}
	if failed {
		return errRejected
	}
	return nil
}

// Run is reachable only from the explicit debug command after the existing full
// root verifier. It never enters a namespace, brings a link up, assigns an IP,
// alters firewall rules, executes a package program, or contacts a peer.
func Run(ctx context.Context) string {
	ctx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	return exercise(ctx, &controller{ctx: ctx}).record()
}

// Child is a fixed protocol participant, not an operator-facing helper. A
// same-executable parent and a distinct network namespace are mandatory.
func Child(role byte) int {
	if role > 1 || os.Geteuid() != 0 || os.Getegid() != 0 {
		return 1
	}
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	deadline, _ := ctx.Deadline()
	timer := time.AfterFunc(13*time.Second, func() { os.Exit(1) })
	defer timer.Stop()
	self, e1 := os.Stat("/proc/self/exe")
	parent, e2 := os.Stat("/proc/" + strconv.Itoa(os.Getppid()) + "/exe")
	if e1 != nil || e2 != nil || !os.SameFile(self, parent) {
		return 1
	}
	f, id, err := openNamespace(os.Getpid())
	if err != nil {
		return 1
	}
	defer f.Close()
	p, parentID, err := openNamespace(os.Getppid())
	if err != nil {
		return 1
	}
	defer p.Close()
	if id == parentID {
		return 1
	}
	file := os.NewFile(3, "control")
	conn, err := channel(file, deadline)
	file.Close()
	if err != nil {
		return 1
	}
	defer conn.Close()
	route, err := openRoute(ctx)
	if err != nil {
		return 1
	}
	defer syscall.Close(route.fd)
	if route.empty() != nil {
		return 1
	}
	rights := syscall.UnixRights(int(f.Fd()))
	n, oob, err := conn.WriteMsgUnix([]byte{'R'}, rights, nil)
	if err != nil || n != 1 || oob != len(rights) {
		return 1
	}
	command := func(want byte) bool { b, err := readPacket(conn, 1); return err == nil && b[0] == want }
	if !command('I') {
		return 1
	}
	links, err := route.links()
	if err != nil {
		return 1
	}
	end, err := inspectLinks(links, role)
	if err != nil || route.noAddressesOrRoutes() != nil || writePacket(conn, append(u32(end.index), u32(end.peer)...)) != nil {
		return 1
	}
	if role == 0 {
		if !command('D') {
			return 1
		}
		links, err := route.links()
		if err != nil {
			return 1
		}
		again, err := inspectLinks(links, role)
		if err != nil || again != end {
			return 1
		}
		if _, err := route.transaction(delLink, ackFlag, 0, linkInfo(end.index)); err != nil || writePacket(conn, []byte{'D'}) != nil {
			return 1
		}
	}
	if !command('E') || route.empty() != nil || writePacket(conn, []byte{'E'}) != nil || !command('X') || writePacket(conn, []byte{'X'}) != nil {
		return 1
	}
	return 0
}

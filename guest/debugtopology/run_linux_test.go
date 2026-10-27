//go:build linux && amd64

// SPDX-License-Identifier: Apache-2.0
package debugtopology

import (
	"net"
	"os"
	"syscall"
	"testing"
	"time"
)

// Only local Unix IPC and read-only namespace descriptors are used here. No
// clone, setns, route socket, link mutation, package program or child executes.
func testChannel(t *testing.T) (*net.UnixConn, *net.UnixConn) {
	t.Helper()
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_SEQPACKET|syscall.SOCK_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	var out [2]*net.UnixConn
	for i, fd := range fds {
		f := os.NewFile(uintptr(fd), "test-control")
		out[i], err = channel(f, time.Now().Add(time.Second))
		f.Close()
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { out[i].Close() })
	}
	return out[0], out[1]
}

func TestNamespaceDescriptorTransferIsBoundToOpenedObject(t *testing.T) {
	f, id, err := openNamespace(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	a, b := testChannel(t)
	rights := syscall.UnixRights(int(f.Fd()))
	if n, oob, err := a.WriteMsgUnix([]byte{'R'}, rights, nil); err != nil || n != 1 || oob != len(rights) {
		t.Fatal(err)
	}
	received, got, err := receiveNamespace(b)
	if err != nil {
		t.Fatal(err)
	}
	defer received.Close()
	if got != id || received.Fd() == f.Fd() {
		t.Fatal("wrong transferred object")
	}
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, received.Fd(), syscall.F_GETFD, 0)
	if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
		t.Fatal("inheritable namespace")
	}
}

func TestNamespaceTransferRejectsMissingExtraWrongTypeAndTruncatedPackets(t *testing.T) {
	f, _, err := openNamespace(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	ordinary, err := os.CreateTemp(t.TempDir(), "ordinary")
	if err != nil {
		t.Fatal(err)
	}
	defer ordinary.Close()
	for _, test := range []struct {
		name string
		data []byte
		fds  []int
	}{
		{"missing", []byte{'R'}, nil},
		{"extra", []byte{'R'}, []int{int(f.Fd()), int(f.Fd())}},
		{"wrong-type", []byte{'R'}, []int{int(ordinary.Fd())}},
		{"wrong-token", []byte{'X'}, []int{int(f.Fd())}},
		{"truncated-data", make([]byte, 64), []int{int(f.Fd())}},
		{"truncated-rights", []byte{'R'}, []int{int(f.Fd()), int(f.Fd()), int(f.Fd()), int(f.Fd()), int(f.Fd()), int(f.Fd())}},
	} {
		t.Run(test.name, func(t *testing.T) {
			a, b := testChannel(t)
			var control []byte
			if len(test.fds) > 0 {
				control = syscall.UnixRights(test.fds...)
			}
			if _, _, err := a.WriteMsgUnix(test.data, control, nil); err != nil {
				t.Fatal(err)
			}
			if received, _, err := receiveNamespace(b); err == nil {
				received.Close()
				t.Fatal("invalid namespace accepted")
			}
		})
	}
}

func TestControlProtocolRejectsExtraBytesDescriptorsAndDeadline(t *testing.T) {
	for _, test := range []string{"extra", "rights", "deadline"} {
		t.Run(test, func(t *testing.T) {
			a, b := testChannel(t)
			switch test {
			case "extra":
				if writePacket(a, []byte("II")) != nil {
					t.Fatal("setup")
				}
			case "rights":
				f, err := os.Open("/dev/null")
				if err != nil {
					t.Fatal(err)
				}
				defer f.Close()
				if _, _, err := a.WriteMsgUnix([]byte{'I'}, syscall.UnixRights(int(f.Fd())), nil); err != nil {
					t.Fatal(err)
				}
			case "deadline":
				if b.SetDeadline(time.Now().Add(-time.Second)) != nil {
					t.Fatal("setup")
				}
			}
			if _, err := readPacket(b, 1); err == nil {
				t.Fatal("invalid control packet")
			}
		})
	}
}

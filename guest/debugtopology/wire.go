// SPDX-License-Identifier: Apache-2.0
package debugtopology

import (
	"bytes"
	"encoding/binary"
)

// Linux v6.8 UAPI at e8f897f4afef0031fe618a8e94127a0934896aba:
// include/uapi/linux/{netlink,rtnetlink,if_link,veth}.h and
// drivers/net/veth.c:veth_newlink. Only linux/amd64 executes these LE messages.
const (
	newLink         = 16
	delLink         = 17
	getLink         = 18
	getAddr         = 22
	getRoute        = 26
	requestFlag     = 1
	ackFlag         = 4
	dumpFlag        = 0x300
	createExclusive = 0x600
	nested          = 0x8000
)

func u32(v uint32) []byte { return binary.LittleEndian.AppendUint32(nil, v) }
func align(n int) int     { return (n + 3) &^ 3 }

func attribute(kind uint16, value []byte) []byte {
	b := make([]byte, align(4+len(value)))
	binary.LittleEndian.PutUint16(b, uint16(4+len(value)))
	binary.LittleEndian.PutUint16(b[2:], kind)
	copy(b[4:], value)
	return b
}

func linkInfo(index uint32) []byte {
	b := make([]byte, 16)
	binary.LittleEndian.PutUint32(b[4:], index)
	return b // flags and change mask zero: no operation can bring a link up.
}

func createPair(subjectFD, peerFD uint32) ([]byte, error) {
	if subjectFD < 3 || peerFD < 3 || subjectFD == peerFD || subjectFD > 1<<20 || peerFD > 1<<20 {
		return nil, errRejected
	}
	end := func(name string, mac [6]byte, fd uint32) []byte {
		b := linkInfo(0)
		b = append(b, attribute(3, append([]byte(name), 0))...)
		b = append(b, attribute(1, mac[:])...)
		return append(b, attribute(28, u32(fd))...) // IFLA_NET_NS_FD, never a PID.
	}
	peer := attribute(1|nested, end(peerName, peerMAC, peerFD)) // VETH_INFO_PEER
	info := append(attribute(1, []byte("veth\x00")), attribute(2|nested, peer)...)
	return append(end(subjectName, subjectMAC, subjectFD), attribute(18|nested, info)...), nil
}

func message(kind, flags uint16, seq uint32, payload []byte) []byte {
	b := make([]byte, 16+len(payload))
	binary.LittleEndian.PutUint32(b, uint32(len(b)))
	binary.LittleEndian.PutUint16(b[4:], kind)
	binary.LittleEndian.PutUint16(b[6:], flags|requestFlag)
	binary.LittleEndian.PutUint32(b[8:], seq)
	copy(b[16:], payload)
	return b
}

func attributes(raw []byte) (map[uint16][]byte, error) {
	if len(raw) > 32768 {
		return nil, errRejected
	}
	out := make(map[uint16][]byte)
	for len(raw) > 0 {
		if len(raw) < 4 || len(out) >= 128 {
			return nil, errRejected
		}
		n, kind := int(binary.LittleEndian.Uint16(raw)), binary.LittleEndian.Uint16(raw[2:])
		key := kind & 0x3fff
		_, duplicate := out[key]
		if n < 4 || align(n) > len(raw) || kind&0x4000 != 0 || key == 0 || duplicate || !zero(raw[n:align(n)]) {
			return nil, errRejected
		}
		out[key] = raw[4:n]
		raw = raw[align(n):]
	}
	return out, nil
}

func zero(b []byte) bool { return bytes.Equal(b, make([]byte, len(b))) }

type link struct {
	index, flags, mtu, peer uint32
	kind                    uint16
	name                    string
	mac                     [6]byte
	veth                    bool
}

func parseLink(raw []byte) (link, error) {
	if len(raw) < 16 || raw[0] != 0 || raw[1] != 0 {
		return link{}, errRejected
	}
	a, err := attributes(raw[16:])
	name := a[3]
	if err != nil || len(name) < 2 || len(name) > 16 || name[len(name)-1] != 0 || bytes.ContainsRune(name[:len(name)-1], 0) ||
		len(a[1]) != 6 || len(a[4]) != 4 {
		return link{}, errRejected
	}
	l := link{index: binary.LittleEndian.Uint32(raw[4:]), flags: binary.LittleEndian.Uint32(raw[8:]),
		kind: binary.LittleEndian.Uint16(raw[2:]), mtu: binary.LittleEndian.Uint32(a[4]), name: string(name[:len(name)-1])}
	copy(l.mac[:], a[1])
	if l.index == 0 || l.index > 0x7fffffff || l.mtu == 0 {
		return link{}, errRejected
	}
	if v, ok := a[5]; ok {
		if len(v) != 4 {
			return link{}, errRejected
		}
		l.peer = binary.LittleEndian.Uint32(v)
	}
	if v, ok := a[18]; ok {
		info, err := attributes(v)
		if err != nil || !bytes.Equal(info[1], []byte("veth\x00")) {
			return link{}, errRejected
		}
		l.veth = true
	}
	return l, nil
}

func loopback(l link, fresh bool) bool {
	allowed := uint32(8 | 1 | 0x40 | 0x10000) // LOOPBACK, UP, RUNNING, LOWER_UP
	return l.index == 1 && l.name == "lo" && l.kind == 772 && l.mac == [6]byte{} && !l.veth && l.peer == 0 &&
		l.flags&8 != 0 && l.flags & ^allowed == 0 && (!fresh || l.flags == 8)
}

func inspectLinks(links []link, role byte) (pairEnd, error) {
	if len(links) != 2 || role > 1 {
		return pairEnd{}, errRejected
	}
	if links[1].name == "lo" {
		links[0], links[1] = links[1], links[0]
	}
	l := links[1]
	name, mac := subjectName, subjectMAC
	if role == 1 {
		name, mac = peerName, peerMAC
	}
	if !loopback(links[0], true) || l.index <= 1 || l.peer <= 1 || l.name != name || l.mac != mac ||
		l.kind != 1 || !l.veth || l.flags != 0x1002 || l.mtu != 1500 {
		return pairEnd{}, errRejected
	}
	return pairEnd{l.index, l.peer}, nil
}

// A transaction has a byte/message/datagram bound, kernel-only sender, exact
// sequence/port binding, and an explicit non-interrupted terminal record.
type replies struct {
	request      []byte
	port         uint32
	dump         bool
	expected     uint16
	bytes, count int
	done         bool
	data         [][]byte
}

func (r *replies) accept(raw []byte) error {
	if r.done || len(raw) == 0 || r.bytes+len(raw) > 65536 {
		return errRejected
	}
	r.bytes += len(raw)
	for len(raw) != 0 {
		if len(raw) < 16 || r.count >= 32 || r.done {
			return errRejected
		}
		r.count++
		n := int(binary.LittleEndian.Uint32(raw))
		if n < 16 || align(n) > len(raw) || !zero(raw[n:align(n)]) {
			return errRejected
		}
		h := raw[:16]
		kind, flags := binary.LittleEndian.Uint16(h[4:]), binary.LittleEndian.Uint16(h[6:])
		if binary.LittleEndian.Uint32(h[8:]) != binary.LittleEndian.Uint32(r.request[8:]) || binary.LittleEndian.Uint32(h[12:]) != r.port {
			return errRejected
		}
		body := raw[16:n]
		switch {
		case !r.dump && kind == 2: // NLMSG_ERROR, zero is the only accepted ACK.
			if flags & ^uint16(0x100) != 0 || len(body) != 20 || !zero(body[:4]) || !bytes.Equal(body[4:], r.request[:16]) {
				return errRejected
			}
			r.done = true
		case r.dump && kind == 3:
			if flags != 2 || len(body) != 4 || !zero(body) {
				return errRejected
			}
			r.done = true
		case r.dump && kind == r.expected:
			if flags != 2 || len(r.data) >= 4 {
				return errRejected
			}
			r.data = append(r.data, append([]byte(nil), body...))
		default:
			return errRejected
		}
		raw = raw[align(n):]
	}
	return nil
}

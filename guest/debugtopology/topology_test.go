// SPDX-License-Identifier: Apache-2.0
package debugtopology

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

type fakeBoundary struct {
	calls       []string
	fail        string
	same        bool
	badPair     bool
	cleanupFail bool
	cancel      context.CancelFunc
	cancelAt    string
}

func (f *fakeBoundary) call(s string) error {
	f.calls = append(f.calls, s)
	if s == f.fail {
		return errRejected
	}
	if f.cancel != nil && s == f.cancelAt {
		f.cancel()
	}
	return nil
}
func (f *fakeBoundary) initial() error { return f.call("initial") }
func (f *fakeBoundary) spawn(role byte) (namespace, error) {
	n := uint64(role) + 10
	if f.same {
		n = 10
	}
	return namespace{1, n}, f.call("spawn" + string('0'+role))
}
func (f *fakeBoundary) create() error { return f.call("create") }
func (f *fakeBoundary) inspect(role byte) (pairEnd, error) {
	end := pairEnd{2, 3}
	if role == 1 {
		end = pairEnd{3, 2}
	}
	if f.badPair {
		end.peer++
	}
	return end, f.call("inspect" + string('0'+role))
}
func (f *fakeBoundary) remove() error         { return f.call("remove") }
func (f *fakeBoundary) empty(role byte) error { return f.call("empty" + string('0'+role)) }
func (f *fakeBoundary) unchanged() error      { return f.call("unchanged") }
func (f *fakeBoundary) cleanup() error {
	err := f.call("cleanup")
	if f.cleanupFail {
		return errRejected
	}
	return err
}

var ordered = []string{"initial", "spawn0", "spawn1", "create", "inspect0", "inspect1", "remove", "empty0", "empty1", "unchanged", "cleanup"}

func TestLifecycleOrderedDownOnlyPairAndCleanup(t *testing.T) {
	f := &fakeBoundary{}
	out := exercise(context.Background(), f)
	if out.stage != 0 || !out.cleaned || !reflect.DeepEqual(f.calls, ordered) || out.record() != "NIB_DEBUG_TOPOLOGY_V1 PASS\n" {
		t.Fatal(out, f.calls)
	}
}

func TestEveryFailureCleansOnlyOwnedResourcesAndCannotPass(t *testing.T) {
	for i, at := range ordered {
		t.Run(at, func(t *testing.T) {
			f := &fakeBoundary{fail: at}
			out := exercise(context.Background(), f)
			want := append(append([]string{}, ordered[:i+1]...), "cleanup")
			if at == "cleanup" {
				want = ordered
			}
			if out.stage == 0 || out.cleaned != (at != "cleanup") || !reflect.DeepEqual(f.calls, want) || strings.Contains(out.record(), "PASS") {
				t.Fatal(out, f.calls)
			}
		})
	}
	for _, f := range []*fakeBoundary{{same: true}, {badPair: true}, {cleanupFail: true}} {
		out := exercise(context.Background(), f)
		if out.stage == 0 || f.calls[len(f.calls)-1] != "cleanup" {
			t.Fatal(out, f.calls)
		}
	}
}

func TestCancellationNeverStartsNextStageAndStillCleans(t *testing.T) {
	for _, early := range []bool{true, false} {
		ctx, cancel := context.WithCancel(context.Background())
		f := &fakeBoundary{cancel: cancel, cancelAt: "create"}
		if early {
			cancel()
		}
		out := exercise(ctx, f)
		cancel()
		if out.stage == 0 || !out.cleaned {
			t.Fatal(out)
		}
		if early && !reflect.DeepEqual(f.calls, []string{"cleanup"}) {
			t.Fatal(f.calls)
		}
		if !early && !reflect.DeepEqual(f.calls, []string{"initial", "spawn0", "spawn1", "create", "cleanup"}) {
			t.Fatal(f.calls)
		}
	}
}

func TestCancellationDuringCleanupCannotPass(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	f := &fakeBoundary{cancelAt: "cleanup", cancel: cancel}
	out := exercise(ctx, f)
	if out.stage == 0 || !out.cleaned {
		t.Fatal(out)
	}
}

func TestRecordsBoundedAndSeparateFromBootProof(t *testing.T) {
	for n := range 256 {
		for _, cleaned := range []bool{false, true} {
			r := (outcome{byte(n), cleaned}).record()
			if len(r) > 32 || !strings.HasPrefix(r, "NIB_DEBUG_TOPOLOGY_V1 ") || strings.Count(r, "\n") != 1 {
				t.Fatal(r)
			}
			if (n == 0 && cleaned) != strings.Contains(r, "PASS") {
				t.Fatal(r)
			}
		}
	}
}

func TestSubjectIdentityMatchesExistingFixture(t *testing.T) {
	raw, err := os.ReadFile("../nftables/testdata/h100-static-plan.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Manifest struct{ Interface, MAC string }
	}
	if json.Unmarshal(raw, &fixture) != nil || fixture.Manifest.Interface != subjectName || fixture.Manifest.MAC != "02:00:00:00:00:01" || subjectMAC != [6]byte{2, 0, 0, 0, 0, 1} {
		t.Fatal("fixture drift")
	}
}

func TestCreateIsFixedDownAndFDTargetedInBothNamespaces(t *testing.T) {
	b, err := createPair(7, 9)
	if err != nil || !zero(b[:16]) {
		t.Fatal(err, b)
	}
	a, err := attributes(b[16:])
	if err != nil {
		t.Fatal(err)
	}
	if len(a) != 4 || string(a[3]) != subjectName+"\x00" || !reflect.DeepEqual(a[1], subjectMAC[:]) || binary.LittleEndian.Uint32(a[28]) != 7 {
		t.Fatal(a)
	}
	info, err := attributes(a[18])
	if err != nil || len(info) != 2 || string(info[1]) != "veth\x00" {
		t.Fatal(info, err)
	}
	data, err := attributes(info[2])
	if err != nil || len(data) != 1 || !zero(data[1][:16]) {
		t.Fatal(data, err)
	}
	peer, err := attributes(data[1][16:])
	if err != nil || len(peer) != 3 || string(peer[3]) != peerName+"\x00" || !reflect.DeepEqual(peer[1], peerMAC[:]) || binary.LittleEndian.Uint32(peer[28]) != 9 {
		t.Fatal(peer, err)
	}
	for _, fds := range [][2]uint32{{0, 9}, {7, 2}, {7, 7}, {7, 1 << 21}} {
		if _, err := createPair(fds[0], fds[1]); err == nil {
			t.Fatal(fds)
		}
	}
}

func rawLink(index uint32, name string, mac [6]byte, typ uint16, flags uint32, veth bool, peer uint32) []byte {
	b := linkInfo(index)
	binary.LittleEndian.PutUint16(b[2:], typ)
	binary.LittleEndian.PutUint32(b[8:], flags)
	b = append(b, attribute(3, append([]byte(name), 0))...)
	b = append(b, attribute(1, mac[:])...)
	b = append(b, attribute(4, u32(1500))...)
	if peer != 0 {
		b = append(b, attribute(5, u32(peer))...)
	}
	if veth {
		b = append(b, attribute(18, attribute(1, []byte("veth\x00")))...)
	}
	return b
}

func goodLinks(t *testing.T) []link {
	t.Helper()
	lo, err := parseLink(rawLink(1, "lo", [6]byte{}, 772, 8, false, 0))
	if err != nil {
		t.Fatal(err)
	}
	subject, err := parseLink(rawLink(2, subjectName, subjectMAC, 1, 0x1002, true, 3))
	if err != nil {
		t.Fatal(err)
	}
	return []link{lo, subject}
}

func TestExactReadbackRejectsUpWrongNameMACTypeKindPeerAndExtras(t *testing.T) {
	for _, mutate := range []func([]link) []link{
		func(l []link) []link { l[1].flags |= 1; return l },
		func(l []link) []link { l[0].flags |= 1; return l },
		func(l []link) []link { l[1].flags |= 0x10000; return l },
		func(l []link) []link { l[1].name = "eth0"; return l },
		func(l []link) []link { l[1].mac[5]++; return l },
		func(l []link) []link { l[1].kind = 772; return l },
		func(l []link) []link { l[1].veth = false; return l },
		func(l []link) []link { l[1].peer = 1; return l },
		func(l []link) []link { l[1].mtu++; return l },
		func(l []link) []link { return append(l, l[1]) },
		func(l []link) []link { return l[:1] },
	} {
		if _, err := inspectLinks(mutate(goodLinks(t)), 0); err == nil {
			t.Fatal("unsafe topology accepted")
		}
	}
	l := goodLinks(t)
	if got, err := inspectLinks(l, 0); err != nil || got != (pairEnd{2, 3}) {
		t.Fatal(got, err)
	}
	if _, err := inspectLinks([]link{l[1], l[0]}, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := inspectLinks(l, 1); err == nil {
		t.Fatal("wrong role")
	}
}

func TestMalformedAttributesAndLinkHeaders(t *testing.T) {
	for _, raw := range [][]byte{{1}, {0, 0, 1, 0}, {8, 0, 1, 0}, append(attribute(3, []byte("lo\x00")), attribute(3, nil)...), append(attribute(3, nil), attribute(3, nil)...), {5, 0, 3, 0, 0, 1, 0, 0}, {4, 0, 1, 0x40}} {
		if _, err := attributes(raw); err == nil {
			t.Fatalf("accepted %x", raw)
		}
	}
	b := rawLink(2, subjectName, subjectMAC, 1, 0x1002, true, 3)
	for n := range 16 {
		if _, err := parseLink(b[:n]); err == nil {
			t.Fatal(n)
		}
	}
	for _, mutate := range []func([]byte) []byte{
		func(b []byte) []byte { b[0] = 2; return b },
		func(b []byte) []byte { b[1] = 1; return b },
		func(b []byte) []byte { binary.LittleEndian.PutUint32(b[4:], 0); return b },
		func(b []byte) []byte { return append(b, attribute(4, u32(9000))...) },
		func(b []byte) []byte { return append(b, attribute(3, []byte("bad\x00name\x00"))...) },
	} {
		if _, err := parseLink(mutate(append([]byte{}, b...))); err == nil {
			t.Fatal("bad link")
		}
	}
}

func reply(kind, flags uint16, body []byte) []byte {
	b := message(kind, 0, 1, body)
	binary.LittleEndian.PutUint16(b[6:], flags)
	binary.LittleEndian.PutUint32(b[12:], 42)
	return b
}

func TestTransactionExactKernelReplyAndTerminal(t *testing.T) {
	req := message(getLink, dumpFlag, 1, linkInfo(0))
	r := replies{request: req, port: 42, dump: true, expected: newLink}
	data := reply(newLink, 2, rawLink(1, "lo", [6]byte{}, 772, 8, false, 0))
	if r.accept(data) != nil || r.done || len(r.data) != 1 {
		t.Fatal(r)
	}
	if r.accept(reply(3, 2, u32(0))) != nil || !r.done {
		t.Fatal(r)
	}
	if r.accept(reply(3, 2, u32(0))) == nil {
		t.Fatal("extra terminal accepted")
	}
	req = message(newLink, ackFlag|createExclusive, 1, linkInfo(0))
	r = replies{request: req, port: 42}
	if r.accept(reply(2, 0x100, append(u32(0), req[:16]...))) != nil || !r.done {
		t.Fatal(r)
	}
}

func TestTransactionsFailClosedOnTruncationBindingErrorsAndLimits(t *testing.T) {
	req := message(getLink, dumpFlag, 1, linkInfo(0))
	done := reply(3, 2, u32(0))
	for _, mutate := range []func([]byte) []byte{
		func(b []byte) []byte { return b[:15] },
		func(b []byte) []byte { b[0] = 255; return b },
		func(b []byte) []byte { b[8]++; return b },
		func(b []byte) []byte { b[12]++; return b },
		func(b []byte) []byte { b[6] |= 0x10; return b },
		func(b []byte) []byte { b[6] |= 0x20; return b },
		func(b []byte) []byte { b[16] = 1; return b },
		func(b []byte) []byte { return append(b, b...) },
		func(b []byte) []byte { return append(b, 0) },
	} {
		r := replies{request: req, port: 42, dump: true, expected: newLink}
		if r.accept(mutate(append([]byte{}, done...))) == nil {
			t.Fatal("bad terminal")
		}
	}
	for _, r := range []replies{{bytes: 65536}, {count: 32}, {done: true}} {
		r.request = req
		r.port = 42
		r.dump = true
		r.expected = newLink
		if r.accept(done) == nil {
			t.Fatal("bound exceeded")
		}
	}
	r := replies{request: req, port: 42, dump: true, expected: newLink}
	for range 4 {
		if r.accept(reply(newLink, 2, []byte{0, 0, 0, 0})) != nil {
			t.Fatal("test setup")
		}
	}
	if r.accept(reply(newLink, 2, []byte{0, 0, 0, 0})) == nil {
		t.Fatal("too many objects")
	}
	req = message(newLink, ackFlag|createExclusive, 1, linkInfo(0))
	for _, body := range [][]byte{append(u32(1), req[:16]...), append(u32(0), req[:15]...), append(u32(0), make([]byte, 16)...)} {
		r := replies{request: req, port: 42}
		if r.accept(reply(2, 0, body)) == nil {
			t.Fatal("bad ACK")
		}
	}
}

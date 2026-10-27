// SPDX-License-Identifier: Apache-2.0
// Package debugtopology owns one down-only synthetic veth pair. It is not a
// firewall, connectivity, networkd, image-admission or confidential proof.
package debugtopology

import (
	"context"
	"errors"
)

var errRejected = errors.New("debug topology rejected")

const (
	subjectName = "enp1s0"
	peerName    = "nibpeer0"
)

var subjectMAC = [6]byte{2, 0, 0, 0, 0, 1}
var peerMAC = [6]byte{2, 0, 0, 0, 0, 2}

type namespace struct{ device, inode uint64 }
type pairEnd struct{ index, peer uint32 }

// All operations are fixed; neither an interface name nor a command/address can
// come from the caller. The only implementation is the owned Linux controller.
type boundary interface {
	initial() error
	spawn(role byte) (namespace, error)
	create() error
	inspect(role byte) (pairEnd, error)
	remove() error
	empty(role byte) error
	unchanged() error
	cleanup() error
}

type outcome struct {
	stage   byte
	cleaned bool
}

func exercise(ctx context.Context, b boundary) (out outcome) {
	defer func() {
		out.cleaned = b.cleanup() == nil
		if ctx.Err() != nil && out.stage == 0 {
			out.stage = 9
		}
		if !out.cleaned && out.stage == 0 {
			out.stage = 10
		}
	}()
	step := func(stage byte, f func() error) bool {
		out.stage = stage
		return ctx.Err() == nil && f() == nil && ctx.Err() == nil
	}
	if !step(1, b.initial) {
		return
	}
	var ids [2]namespace
	for role := range byte(2) {
		if !step(2+role, func() (err error) { ids[role], err = b.spawn(role); return }) {
			return
		}
	}
	if ids[0] == ids[1] || ids[0].inode == 0 || ids[1].inode == 0 {
		return
	}
	if !step(4, b.create) {
		return
	}
	var ends [2]pairEnd
	for role := range byte(2) {
		if !step(5+role, func() (err error) { ends[role], err = b.inspect(role); return }) {
			return
		}
	}
	if ends[0].index <= 1 || ends[1].index <= 1 || ends[0].peer != ends[1].index || ends[1].peer != ends[0].index {
		return
	}
	if !step(7, b.remove) || !step(8, func() error {
		if b.empty(0) != nil || b.empty(1) != nil {
			return errRejected
		}
		return nil
	}) || !step(9, b.unchanged) {
		return
	}
	out.stage = 0
	return
}

// A separate, fixed grammar deliberately cannot pass the existing boot parser.
func (o outcome) record() string {
	if o.stage == 0 && o.cleaned {
		return "NIB_DEBUG_TOPOLOGY_V1 PASS\n"
	}
	stage := o.stage
	if stage == 0 || stage > 10 {
		stage = 10
	}
	cleaned := byte('0')
	if o.cleaned {
		cleaned = '1'
	}
	return "NIB_DEBUG_TOPOLOGY_V1 F " + string([]byte{'0' + stage/10, '0' + stage%10, ' ', cleaned, '\n'})
}

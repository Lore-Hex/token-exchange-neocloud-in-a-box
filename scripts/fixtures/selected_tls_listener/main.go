// SPDX-License-Identifier: Apache-2.0
// Test-only Go wildcard listener, using the same Listen network/address shape
// as pinned trcs. It accepts no HTTP, TLS or model traffic.
package main

import (
	"encoding/json"
	"io"
	"net"
	"os"
	"runtime"
	"time"
)

func run() error {
	if runtime.Version() != "go1.27.2" || len(os.Args) != 3 || (os.Args[1] != "tcp" && os.Args[1] != "tcp4") {
		return os.ErrInvalid
	}
	listener, err := net.Listen(os.Args[1], "0.0.0.0:0")
	if err != nil {
		return err
	}
	defer listener.Close()
	address := listener.Addr().(*net.TCPAddr)
	metadata, err := json.Marshal(struct {
		Port int  `json:"port"`
		IPv6 bool `json:"ipv6"`
	}{address.Port, address.IP.To4() == nil})
	if err != nil {
		return err
	}
	if err := os.WriteFile(os.Args[2]+".tmp", metadata, 0600); err != nil {
		return err
	}
	if err := os.Rename(os.Args[2]+".tmp", os.Args[2]); err != nil {
		return err
	}
	connection, err := listener.Accept()
	if err != nil {
		return err
	}
	defer connection.Close()
	_, err = io.Copy(io.Discard, connection)
	return err
}

func main() {
	time.AfterFunc(30*time.Second, func() { os.Exit(1) })
	if run() != nil {
		os.Exit(1)
	}
}

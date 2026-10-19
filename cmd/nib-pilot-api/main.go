// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/pilotapi"
)

func run() int {
	flags := flag.NewFlagSet("nib-pilot-api", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	keyFile := flags.String("api-key-file", "", "private key file")
	inputPrice := flags.String("input-price", "", "USD per million input tokens")
	outputPrice := flags.String("output-price", "", "USD per million output tokens")
	retirement := flags.String("retirement-at", "", "fixed UTC retirement for a public pilot")
	if flags.Parse(os.Args[1:]) != nil || flags.NArg() != 0 {
		return 1
	}
	key, err := os.ReadFile(*keyFile)
	if err != nil {
		return 1
	}
	handler, err := pilotapi.New(strings.TrimSuffix(string(key), "\n"), *inputPrice, *outputPrice, *retirement)
	if err != nil {
		return 1
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Addr: "127.0.0.1:8001", Handler: handler,
		ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second,
		MaxHeaderBytes: 16384, ErrorLog: log.New(io.Discard, "", 0)}
	listener, err := net.Listen("tcp", server.Addr)
	if err != nil {
		return 1
	}
	if serve(ctx, server, listener) != nil {
		return 1
	}
	return 0
}

func serve(ctx context.Context, server *http.Server, listener net.Listener) error {
	served := make(chan error, 1)
	go func() { served <- server.Serve(listener) }()
	select {
	case err := <-served:
		return err
	case <-ctx.Done():
	}
	// Await handlers, not just the accept loop: Serve returns as soon as
	// Shutdown closes the listener, before active streams have drained.
	shutdown, cancel := context.WithTimeout(context.Background(), 130*time.Second)
	defer cancel()
	err := server.Shutdown(shutdown)
	if err != nil {
		_ = server.Close()
	}
	<-served
	return err
}

func main() { os.Exit(run()) }

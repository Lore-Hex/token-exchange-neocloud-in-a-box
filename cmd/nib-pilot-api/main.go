// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"errors"
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

func configuredHandler(arguments []string) (*pilotapi.Handler, error) {
	flags := flag.NewFlagSet("nib-pilot-api", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	profile := flags.String("profile", pilotapi.ProfileQwen, "closed API profile: qwen, selected-deepseek, selected-deepseek-graphs, selected-deepseek-quality, selected-deepseek-long or selected-deepseek-million")
	keyFile := flags.String("api-key-file", "", "private key file")
	providerFile := flags.String("provider-config", "", "required operator metadata JSON file")
	inputPrice := flags.String("input-price", "", "USD per million input tokens")
	outputPrice := flags.String("output-price", "", "USD per million output tokens")
	retirement := flags.String("retirement-at", "", "fixed UTC retirement for a public pilot")
	public := flags.Bool("public", false, "require public operator metadata and retirement (listener stays loopback)")
	if err := flags.Parse(arguments); err != nil {
		return nil, err
	}
	if flags.NArg() != 0 {
		return nil, errors.New("unexpected arguments")
	}
	provider, err := pilotapi.LoadProviderConfig(*providerFile)
	if err != nil {
		return nil, err
	}
	if *public {
		if err := provider.ValidatePublic(); err != nil {
			return nil, err
		}
		if *retirement == "" {
			return nil, errors.New("public retirement required")
		}
	}
	key, err := os.ReadFile(*keyFile)
	if err != nil {
		return nil, err
	}
	return pilotapi.NewWithProfile(*profile, provider, strings.TrimSuffix(string(key), "\n"), *inputPrice, *outputPrice, *retirement)
}

func run() int {
	handler, err := configuredHandler(os.Args[1:])
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
	if serve(ctx, server, listener, handler.ShutdownGrace()) != nil {
		return 1
	}
	return 0
}

func serve(ctx context.Context, server *http.Server, listener net.Listener, grace time.Duration) error {
	served := make(chan error, 1)
	go func() { served <- server.Serve(listener) }()
	select {
	case err := <-served:
		return err
	case <-ctx.Done():
	}
	// Await handlers, not just the accept loop: Serve returns as soon as
	// Shutdown closes the listener, before active streams have drained.
	shutdown, cancel := context.WithTimeout(context.Background(), grace)
	defer cancel()
	err := server.Shutdown(shutdown)
	if err != nil {
		_ = server.Close()
	}
	<-served
	return err
}

func main() { os.Exit(run()) }

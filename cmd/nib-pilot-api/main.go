// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"flag"
	"io"
	"log"
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
	if flags.Parse(os.Args[1:]) != nil || flags.NArg() != 0 {
		return 1
	}
	key, err := os.ReadFile(*keyFile)
	if err != nil {
		return 1
	}
	handler, err := pilotapi.New(strings.TrimSuffix(string(key), "\n"), *inputPrice, *outputPrice)
	if err != nil {
		return 1
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Addr: "127.0.0.1:8001", Handler: handler,
		ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second,
		MaxHeaderBytes: 16384, ErrorLog: log.New(io.Discard, "", 0)}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = server.Shutdown(shutdown)
		case <-done:
		}
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return 1
	}
	return 0
}

func main() { os.Exit(run()) }

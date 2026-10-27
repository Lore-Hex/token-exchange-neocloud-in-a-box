// SPDX-License-Identifier: Apache-2.0
// nib-elf-metadata reads one bounded stdin ELF; never a filename or subprocess.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/elfmeta"
)

const deadline = 10 * time.Second

func inspect(input io.Reader, args []string) ([]byte, error) {
	if len(args) != 0 {
		return nil, elfmeta.ErrRejected
	}
	raw, err := io.ReadAll(io.LimitReader(input, elfmeta.MaxBytes+1))
	if err != nil {
		return nil, elfmeta.ErrRejected
	}
	m, err := elfmeta.Read(raw)
	if err != nil {
		return nil, err
	}
	out, err := json.Marshal(m)
	if err != nil || len(out) > 65536 {
		return nil, elfmeta.ErrRejected
	}
	return append(out, '\n'), nil
}

func main() {
	timer := time.AfterFunc(deadline, func() { os.Exit(1) })
	defer timer.Stop()
	out, err := inspect(os.Stdin, os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "ELF metadata rejected; no input binary executed.")
		os.Exit(1)
	}
	if _, err := os.Stdout.Write(out); err != nil {
		os.Exit(1)
	}
}

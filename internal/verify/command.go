// SPDX-License-Identifier: Apache-2.0
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/internal/config"
)

func readPolicyFile(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, errors.New("cannot open policy file")
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > config.MaxDocumentBytes {
		return nil, errors.New("policy must be a bounded regular file")
	}
	b, err := io.ReadAll(io.LimitReader(f, config.MaxDocumentBytes+1))
	if err != nil || len(b) > config.MaxDocumentBytes {
		return nil, errors.New("cannot read bounded policy file")
	}
	return b, nil
}

func Run(args []string, out, errOut io.Writer) int {
	f := flag.NewFlagSet("verify", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	endpoint := f.String("endpoint", "", "HTTPS origin to verify")
	release := f.String("release", "", "locally pinned release manifest")
	releaseHash := f.String("release-sha256", "", "independently trusted manifest digest")
	profile := f.String("profile", "", "locally pinned hardware profile")
	profileHash := f.String("profile-sha256", "", "independently trusted profile digest")
	if f.Parse(args) != nil || f.NArg() != 0 || *endpoint == "" || *release == "" || *releaseHash == "" || *profile == "" || *profileHash == "" {
		fmt.Fprintln(errOut, "usage: nib verify --endpoint <https-origin> --release <file> --release-sha256 <digest> --profile <file> --profile-sha256 <digest>")
		return 2
	}
	releaseBytes, err := readPolicyFile(*release)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	profileBytes, err := readPolicyFile(*profile)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	p, err := LoadPolicy(releaseBytes, *releaseHash, profileBytes, *profileHash)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	observation, err := Fetch(ctx, *endpoint)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	result, checkErr := Check(ctx, observation, p)
	if err := json.NewEncoder(out).Encode(result); err != nil {
		fmt.Fprintln(errOut, "cannot write verification result")
		return 1
	}
	if checkErr != nil {
		fmt.Fprintln(errOut, checkErr)
		return 1
	}
	return 0
}

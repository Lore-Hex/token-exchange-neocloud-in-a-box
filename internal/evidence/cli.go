// SPDX-License-Identifier: Apache-2.0
package evidence

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const usage = "usage: nib evidence new <P0..P9> <slug> | nib evidence scrub --deny-file <path>"

// Run handles arguments after 'nib evidence'. Status is 0 on success, 1 on
// operational failure, 2 on invalid usage. It never echoes invalid arguments,
// the deny-file path, input contents or an underlying operating-system error.
func Run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) == 1 && (args[0] == "--help" || args[0] == "-h") {
		fmt.Fprintln(stdout, usage)
		return 0
	}
	if len(args) != 3 {
		fmt.Fprintln(stderr, usage)
		return 2
	}
	switch args[0] {
	case "new":
		root, err := repositoryRoot(".")
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		dir, err := New(root, args[1], args[2])
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		if _, err := fmt.Fprintln(stdout, dir); err != nil {
			fmt.Fprintln(stderr, "evidence: cannot write scaffold location")
			return 1
		}
		return 0
	case "scrub":
		if args[1] != "--deny-file" || args[2] == "" {
			fmt.Fprintln(stderr, usage)
			return 2
		}
		// Confinement also prevents a changed deny-file symlink from escaping
		// its explicitly supplied parent. Only private regular files are read.
		parent, err := os.OpenRoot(filepath.Dir(args[2]))
		if err != nil {
			fmt.Fprintln(stderr, "evidence: cannot open private deny file")
			return 1
		}
		defer parent.Close()
		name := filepath.Base(args[2])
		info, err := parent.Lstat(name)
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
			fmt.Fprintln(stderr, "evidence: deny file must be a private regular file (0600)")
			return 1
		}
		deny, err := parent.Open(name)
		if err != nil {
			fmt.Fprintln(stderr, "evidence: cannot open private deny file")
			return 1
		}
		defer deny.Close()
		opened, err := deny.Stat()
		if err != nil || !os.SameFile(info, opened) || !opened.Mode().IsRegular() || opened.Mode().Perm()&0o077 != 0 {
			fmt.Fprintln(stderr, "evidence: deny file changed while opening")
			return 1
		}
		if err := Scrub(deny, stdin, stdout); err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		return 0
	default:
		fmt.Fprintln(stderr, usage)
		return 2
	}
}

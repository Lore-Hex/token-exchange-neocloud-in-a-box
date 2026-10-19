# SPDX-License-Identifier: Apache-2.0
"""Run the unmodified checker CLI with socket access limited to loopback.

Its public-schema fetch fails closed to the checker's packaged schema. The Go
generator separately validates the pinned public schema. No check is patched.
"""

import ipaddress
import os
import sys


def loopback_only(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event == "socket.connect":
        host = args[1][0]
    else:
        return
    try:
        allowed = ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise OSError("local catalog proof forbids non-loopback networking")


def main():
    for key in list(os.environ):
        if key.lower().endswith("_proxy") or key == "TR_PROVIDER_API_KEY":
            os.environ.pop(key)
    sys.addaudithook(loopback_only)
    from tr_provider_check.cli import main as checker_main

    return checker_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

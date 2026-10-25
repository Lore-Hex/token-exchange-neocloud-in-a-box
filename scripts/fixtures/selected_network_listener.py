# SPDX-License-Identifier: Apache-2.0
"""Fixed synthetic TCP listener; no HTTP, TLS, credentials or model traffic."""

import socket
import sys
import time


def receive(connection, expected, deadline):
    data = bytearray()
    while len(data) < len(expected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        connection.settimeout(remaining)
        block = connection.recv(len(expected) + 1 - len(data))
        if not block:
            return False
        data.extend(block)
    return data == expected


def main():
    if sys.argv[1:] == ["--probe"]:
        with socket.create_connection(("127.0.0.1", 8443), 1) as connection:
            connection.sendall(b"NIB-PROBE\n")
            return 0 if receive(connection, b"NIB-PROBE-OK\n", time.monotonic() + 1) else 1
    if sys.argv[1:]:
        return 1
    deadline = time.monotonic() + 180
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 8443))
        listener.listen(8)
        listener.settimeout(1)
        while time.monotonic() < deadline:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(1)
                try:
                    if receive(connection, b"NIB-PROBE\n", time.monotonic() + 1):
                        connection.sendall(b"NIB-PROBE-OK\n")
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())

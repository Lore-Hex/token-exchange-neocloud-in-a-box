# SPDX-License-Identifier: Apache-2.0
"""Fixed synthetic TCP listener; no HTTP, TLS, credentials or model traffic."""

import socket
import time


def main():
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
                    if connection.recv(64) == b"NIB-PROBE\n":
                        connection.sendall(b"NIB-PROBE-OK\n")
                except OSError:
                    pass


if __name__ == "__main__":
    main()

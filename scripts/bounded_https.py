# SPDX-License-Identifier: Apache-2.0
"""Single-attempt JSON HTTPS. No redirects, proxy environment, SDK or logging."""

from contextlib import contextmanager
import http.client
import ipaddress
import re
import socket
import ssl
import time

import tau_trial_worker as worker

require = worker.require
LIMIT = 2 << 20


def system_trust():
    import os
    paths = ssl.get_default_verify_paths()
    require(not any(name in os.environ for name in ("SSL_CERT_FILE", "SSL_CERT_DIR",
            paths.openssl_cafile_env, paths.openssl_capath_env)))
    trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    trust.minimum_version = ssl.TLSVersion.TLSv1_2
    trust.load_default_certs(ssl.Purpose.SERVER_AUTH)
    require(trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED and trust.keylog_filename is None)
    return trust


class HTTPS:
    """A frozen host/IPv4 and explicit route set; TLS verification precedes auth."""
    def __init__(self, hostname, paths, *, address=None, clock=time.monotonic):
        require(type(hostname) is str and len(hostname) <= 253
                and re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", hostname))
        require(type(paths) is tuple and 0 < len(paths) <= 2
                and all(path in ("/v1/chat/completions", "/v1/responses", "/v1/responses/input_tokens") for path in paths))
        if address is None:
            require(hostname == "api.openai.com")
        else:
            ip = ipaddress.IPv4Address(address)
            require(str(ip) == address and ip.is_global and not ip.is_multicast)
        self.hostname, self.address, self.paths, self.clock = hostname, address, paths, clock
        self.trust, self.attempts, self.used = system_trust(), dict.fromkeys(paths, 0), False

    def remaining(self, deadline):
        left = deadline - self.clock()
        require(0 < left <= 120)
        return left

    @contextmanager
    def connection(self, deadline):
        # One resolved address, not a retry loop over addresses. The outer alarm
        # covers DNS, TLS, headers and slow-trickle bodies on the main thread.
        address = self.address
        if address is None:
            rows = socket.getaddrinfo(self.hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
            require(rows and len(rows) <= 64)
            address = rows[0][4][0]
            require(ipaddress.IPv4Address(address).is_global)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
            raw.set_inheritable(False)
            raw.settimeout(min(5, self.remaining(deadline)))
            raw.connect((address, 443))
            with self.trust.wrap_socket(raw, server_hostname=self.hostname) as connection:
                connection.settimeout(self.remaining(deadline))
                yield connection

    def post(self, path, body, credential, deadline, *, before_send=lambda: None):
        require(not self.used and path in self.paths and type(credential) is bytes
                and 16 <= len(credential) <= 4096 and all(33 <= byte <= 126 for byte in credential))
        payload = worker.bounded_json(body, 65536)
        self.used = True
        try:
            with worker.alarm(deadline, self.clock), self.connection(deadline) as connection:
                header = ("POST " + path + " HTTP/1.1\r\nHost: " + self.hostname
                    + "\r\nConnection: close\r\nAccept: application/json\r\nAccept-Encoding: identity"
                    + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload))
                    + "\r\nAuthorization: Bearer ").encode("ascii") + credential + b"\r\n\r\n"
                before_send()
                self.attempts[path] += 1
                connection.sendall(header + payload)
                with http.client.HTTPResponse(connection) as response:
                    response.begin()
                    require(response.status == 200 and not response.headers.defects
                            and len(response.getheaders()) <= 64
                            and sum(len(k) + len(v) for k, v in response.getheaders()) <= 65536
                            and response.getheader("Content-Encoding") in (None, "identity")
                            and response.getheader("Content-Type", "").split(";", 1)[0] == "application/json")
                    lengths = response.headers.get_all("Content-Length", [])
                    transfers = response.headers.get_all("Transfer-Encoding", [])
                    require(len(lengths) <= 1 and transfers in ([], ["chunked"])
                            and not (lengths and transfers))
                    if lengths:
                        require(re.fullmatch(r"[0-9]{1,8}", lengths[0]) and int(lengths[0]) <= LIMIT)
                    raw = response.read(LIMIT + 1)
                    require(0 < len(raw) <= LIMIT and (not lengths or len(raw) == int(lengths[0])))
                    self.remaining(deadline)
                    return worker.decode(raw)
        finally:
            self.used = False

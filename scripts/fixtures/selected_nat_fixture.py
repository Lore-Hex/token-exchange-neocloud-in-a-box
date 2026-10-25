# SPDX-License-Identifier: Apache-2.0
"""Fixed synthetic TCP/DNS fixture. Never accepts an arbitrary payload or URL."""

import argparse
import ipaddress
import json
import selectors
import socket
import ssl
import struct
import time

FRAME = b"NIB-NAT-CHECK\n"
ANSWER = b"NIB-NAT-OK\n"
QUERY = struct.pack("!6H", 1977, 0x100, 1, 0, 0, 0) + b"\x03nib\x07invalid\0\0\x01\0\x01"
DNS_ANSWER = struct.pack("!6H", 1977, 0x8183, 1, 0, 0, 0) + QUERY[12:]
PORTS = (53, 8000, 8001, 8443, 8450)


def receive(sock, size, deadline):
    data = bytearray()
    while len(data) < size:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError()
        sock.settimeout(left)
        block = sock.recv(size - len(data))
        if not block:
            raise ValueError("short synthetic frame")
        data.extend(block)
    return bytes(data)


def request(address, port, kind, certificate=None):
    ip = ipaddress.ip_address(address)
    if port not in (*PORTS, 443) or kind not in ("tcp", "tls", "dns-udp", "dns-tcp", "stats"):
        raise ValueError("fixed fixture request rejected")
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    dns = kind.startswith("dns-")
    if dns and port != 53:
        raise ValueError("fixed fixture request rejected")
    deadline = time.monotonic() + 2
    with socket.socket(family, socket.SOCK_DGRAM if kind == "dns-udp" else socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect((address, port))
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError()
        sock.settimeout(left)
        if kind == "tls":
            context = ssl.create_default_context(cafile=certificate)
            with context.wrap_socket(sock, server_hostname="nib.invalid") as secured:
                for _ in range(2):
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError()
                    secured.settimeout(left)
                    secured.sendall(FRAME)
                    if receive(secured, len(ANSWER), deadline) != ANSWER:
                        return False
                return True
        if kind == "dns-udp":
            sock.send(QUERY)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            sock.settimeout(left)
            return sock.recv(512) == DNS_ANSWER
        raw = b"STATS\n" if kind == "stats" else (struct.pack("!H", len(QUERY)) + QUERY if dns else FRAME)
        sock.sendall(raw)
        if kind == "stats":
            size = struct.unpack("!H", receive(sock, 2, deadline))[0]
            if size > 4096:
                raise ValueError("fixture size rejected")
            return json.loads(receive(sock, size, deadline))
        if dns:
            size = struct.unpack("!H", receive(sock, 2, deadline))[0]
            if size > 512:
                raise ValueError("fixture size rejected")
            return receive(sock, size, deadline) == DNS_ANSWER
        return receive(sock, len(ANSWER), deadline) == ANSWER


def serve(addresses, ports, until, certificate=None, private_key=None):
    counts = {"tcp": 0, "dns_udp": 0, "dns_tcp": 0}
    context = None
    if 8443 in ports:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private_key)
    with selectors.DefaultSelector() as selector:
        sockets = []
        try:
            for address in addresses:
                ip = ipaddress.ip_address(address)
                for port in ports:
                    for udp in ((False, True) if port == 53 else (False,)):
                        sock = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                                             socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
                        sockets.append(sock)
                        if ip.version == 6:
                            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind((address, port))
                        if not udp:
                            sock.listen(8)
                        sock.setblocking(False)
                        selector.register(sock, selectors.EVENT_READ, (port, udp))
            while time.monotonic() < until:
                for key, _ in selector.select(min(.2, max(0, until - time.monotonic()))):
                    port, udp = key.data
                    if udp:
                        raw, peer = key.fileobj.recvfrom(513)
                        if raw == QUERY:
                            counts["dns_udp"] += 1
                            key.fileobj.sendto(DNS_ANSWER, peer)
                        continue
                    conn, _ = key.fileobj.accept()
                    conn.settimeout(1)
                    if port == 8443:
                        try:
                            conn = context.wrap_socket(conn, server_side=True)
                        except OSError:
                            conn.close()
                            continue
                    with conn:
                        end = min(until, time.monotonic() + 1)
                        try:
                            if port == 53:
                                size = struct.unpack("!H", receive(conn, 2, end))[0]
                                if size == len(QUERY) and receive(conn, size, end) == QUERY:
                                    counts["dns_tcp"] += 1
                                    conn.sendall(struct.pack("!H", len(DNS_ANSWER)) + DNS_ANSWER)
                            else:
                                prefix = receive(conn, 6, end)
                                if prefix == b"STATS\n":
                                    data = json.dumps(counts, sort_keys=True).encode()
                                    conn.sendall(struct.pack("!H", len(data)) + data)
                                elif prefix + receive(conn, len(FRAME) - 6, end) == FRAME:
                                    counts["tcp"] += 1
                                    conn.sendall(ANSWER)
                                    if port == 8443 and receive(conn, len(FRAME), end) == FRAME:
                                        counts["tcp"] += 1
                                        conn.sendall(ANSWER)
                        except (OSError, ValueError):
                            pass
        finally:
            for sock in sockets:
                sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("serve", "request"))
    parser.add_argument("--address", action="append", required=True)
    parser.add_argument("--port", type=int, choices=(*PORTS, 443), action="append", required=True)
    parser.add_argument("--kind", choices=("tcp", "tls", "dns-udp", "dns-tcp", "stats"), default="tcp")
    parser.add_argument("--until", type=float)
    parser.add_argument("--certificate")
    parser.add_argument("--private-key")
    args = parser.parse_args()
    if args.mode == "serve":
        if args.until is None or not 0 < args.until - time.monotonic() <= 600:
            return 1
        if 443 in args.port:
            return 1
        serve(args.address, args.port, args.until, args.certificate, args.private_key)
        return 0
    if len(args.address) != 1 or len(args.port) != 1:
        return 1
    try:
        value = request(args.address[0], args.port[0], args.kind, args.certificate)
    except (OSError, ValueError):
        value = False
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
"""Closed selected-service NAT policy; no command execution or host discovery."""

from dataclasses import dataclass
import ipaddress
import json
import re


def require(value):
    if not value:
        raise ValueError("selected network contract rejected")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode(raw):
    require(type(raw) is bytes and len(raw) <= 262144)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))


def match(left, right, op="=="):
    return {"match": {"op": op, "left": left, "right": right}}


def meta(key):
    return {"meta": {"key": key}}


def payload(protocol, field):
    return {"payload": {"protocol": protocol, "field": field}}


def ct(key, **fields):
    return {"ct": {"key": key, **fields}}


@dataclass(frozen=True)
class Policy:
    token: str
    bridge: str
    ingress: str
    address: str
    published: str
    port: int

    def __post_init__(self):
        require(type(self.token) is str and re.fullmatch("[0-9a-f]{32}", self.token))
        for name in (self.bridge, self.ingress):
            require(type(name) is str and re.fullmatch("[A-Za-z][A-Za-z0-9_-]{0,14}", name))
        require(self.bridge != self.ingress and self.bridge != "lo" and self.ingress != "lo")
        for value in (self.address, self.published):
            address = ipaddress.IPv4Address(value)
            require(str(address) == value and not (address.is_unspecified or address.is_multicast
                    or address.is_loopback or address.is_link_local))
        require(self.address != self.published and type(self.port) is int and 1 <= self.port <= 65535)

    @property
    def table(self):
        return "nib_" + self.token

    def objects(self, *, canonical=False):
        """Render input or the exact pinned readback form, never normalize actual rules."""
        table = self.table
        objects = []
        def chain(family, name, hook, priority):
            objects.append({"chain": {"family": family, "table": table, "name": name,
                "type": "filter", "hook": hook, "prio": priority, "policy": "accept"}})
        def rule(family, name, expressions, verdict):
            objects.append({"rule": {"family": family, "table": table, "chain": name,
                "expr": expressions + [{verdict: None}]}})
        incoming = match(meta("iifname"), self.bridge)
        outgoing = match(meta("oifname"), self.bridge)
        tcp4 = [match(meta("nfproto"), "ipv4"), match(meta("l4proto"), "tcp")]
        # nft 1.0.9 elides these dependencies only when IP/TCP payloads imply them.
        # Conntrack-only rules below still need both explicit protocol matches.
        payload_tcp4 = [] if canonical else tcp4
        reply = [match(ct("state"), "established", "in"), match(ct("direction"), "reply")]
        dnat = [match(ct("status"), "dnat", "in"),
                match(ct("ip daddr", dir="original"), self.published),
                match(ct("proto-dst", dir="original"), self.port)]
        objects.append({"table": {"family": "inet", "name": table}})
        chain("inet", "input", "input", -10)
        # Conntrack's reply tuple survives reverse NAT before local delivery.
        host_reply = [incoming] + tcp4 + reply + [
            match(ct("ip saddr", dir="reply"), self.address), match(ct("proto-src", dir="reply"), 8443)]
        rule("inet", "input", host_reply + [match(ct("ip daddr", dir="original"), self.address),
            match(ct("proto-dst", dir="original"), 8443)], "accept")
        rule("inet", "input", host_reply + dnat, "accept")
        rule("inet", "input", [incoming], "drop")
        # Also constrain a userland proxy's host listener to the ingress interface.
        rule("inet", "input", payload_tcp4 + [match(payload("ip", "daddr"), self.published),
            match(payload("tcp", "dport"), self.port),
            match(meta("iifname"), self.ingress, "!="), match(meta("iifname"), "lo", "!=")], "drop")
        chain("inet", "forward", "forward", -10)
        for state in ("new", "established"):
            original = [match(ct("state"), state, "in"), match(ct("direction"), "original")]
            rule("inet", "forward", [outgoing, match(meta("iifname"), self.ingress)] + payload_tcp4 + original + dnat + [
                match(payload("ip", "daddr"), self.address), match(payload("tcp", "dport"), 8443)], "accept")
        rule("inet", "forward", [incoming, match(meta("oifname"), self.ingress)] + payload_tcp4 + reply + dnat + [
            match(payload("ip", "saddr"), self.address), match(payload("tcp", "sport"), 8443)], "accept")
        rule("inet", "forward", [incoming], "drop")
        rule("inet", "forward", [outgoing], "drop")
        chain("inet", "output", "output", -10)
        for state in ("new", "established"):
            original = [match(ct("state"), state, "in"), match(ct("direction"), "original")]
            rule("inet", "output", [outgoing] + payload_tcp4 + original + [
                match(payload("ip", "daddr"), self.address), match(payload("tcp", "dport"), 8443)], "accept")
        rule("inet", "output", [outgoing], "drop")
        objects.append({"table": {"family": "bridge", "name": table}})
        chain("bridge", "peers", "forward", -210)
        # Native bridge hooks cover L2 even when bridge-nf-call-iptables is off.
        rule("bridge", "peers", [match(meta("ibrname"), self.bridge)], "drop")
        rule("bridge", "peers", [match(meta("obrname"), self.bridge)], "drop")
        return objects

    def batch(self):
        return encoded({"nftables": [{"create" if "table" in row else "add": row}
                                      for row in self.objects()]})

    def verify(self, raw):
        """Compare owned objects, ignoring only allocated handles and enumeration."""
        value = decode(raw)
        require(type(value) is dict and set(value) == {"nftables"} and type(value["nftables"]) is list)
        actual, versions = [], 0
        for row in value["nftables"]:
            require(type(row) is dict and len(row) == 1)
            kind, body = next(iter(row.items()))
            require(type(body) is dict and kind != "flowtable")
            if kind == "metainfo":
                require(body.get("version") == "1.0.9" and body.get("json_schema_version") == 1)
                versions += 1
                continue
            if body.get("name" if kind == "table" else "table") != self.table:
                continue
            require(kind in ("table", "chain", "rule"))
            actual.append({kind: {key: item for key, item in body.items() if key != "handle"}})
        def grouped(rows):
            groups = {}
            for row in rows:
                kind, body = next(iter(row.items()))
                key = (kind, body["family"], body.get("chain", body.get("name", "")))
                groups.setdefault(key, []).append(row)
            return groups
        require(versions == 1 and grouped(actual) == grouped(self.objects(canonical=True)))

    def remove_batch(self, *, containers_absent, creations_resolved):
        require(containers_absent is True and creations_resolved is True)
        return encoded({"nftables": [{"delete": {"table": {"family": family, "name": self.table}}}
                                     for family in ("bridge", "inet")]})


def check_dns(info):
    """Inherited DNS may relay from the host namespace, bypassing bridge rules."""
    host = info["HostConfig"]
    require(host.get("Dns") == ["127.0.0.1"] and not host.get("DnsSearch")
            and not host.get("DnsOptions") and not host.get("ExtraHosts"))

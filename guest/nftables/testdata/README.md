# Synthetic readback fixture

`readback.json` is a hand-assembled, synthetic complete ruleset for an empty
egress list. It is **not** captured kernel output or evidence of installation.
Tests independently compare it to the expected policy and mutate rules,
ordering, fields, destinations and metadata to exercise fail-closed behavior.

Its shape is grounded in the pinned
[nftables 1.0.9 source archive](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz),
SHA-256 `a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd`:

- `src/json.c`: set 131-213, rule 229-256, chain 258-300, relational expressions
  547-553, set references 640-647, table/ruleset listing 1691-1751, metadata
  1928-1934. Table/chain/set/rule handles are allocated identities, not policy.
- `tests/py/any/ct.t.json:1-19` uses a bare array for comma-list ct-state syntax;
  lines 36-56 use a `set` wrapper for brace syntax. Our renderer uses
  `ct state established,related`, not braces. `src/json.c:698-707` returns a
  list directly; 629-637 wraps a set. The test rejects the unrequested wrapper.
- `tests/py/ip/sets.t.json` and `tests/py/ip6/sets.t.json` show named-set
  references as `@name` with the equality operator. Numeric service output
  is the default in `src/json.c:1109-1119`; no reverse-DNS flag is enabled.
- `configure.ac:1-2` pins the exact version and release-name metadata.

The runtime reads with `--json list ruleset`, deliberately **not** `--numeric`:
`src/main.c:431-433` enables all numeric flags for the latter, and
`src/json.c:922-946` then turns symbolic ct-state values into integers.
Actual pinned Linux/kernel installation and readback still need a separately
reviewed guest execution. This fixture cannot qualify IPv6 neighbor discovery,
routing, connectivity, ongoing policy enforcement or confidential readiness.

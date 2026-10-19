# SPDX-License-Identifier: Apache-2.0

import unittest

from offline_cli import loopback_only


class LoopbackGuardTest(unittest.TestCase):
    def test_allows_only_literal_loopback(self):
        for host in ["127.0.0.1", "::1"]:
            loopback_only("socket.getaddrinfo", (host,))
            loopback_only("socket.connect", (None, (host, 1234)))
        for host in ["trustedrouter.com", "localhost", "192.0.2.1", "::", "0.0.0.0"]:
            with self.assertRaises(OSError):
                loopback_only("socket.getaddrinfo", (host,))
            with self.assertRaises(OSError):
                loopback_only("socket.connect", (None, (host, 443)))


if __name__ == "__main__":
    unittest.main()

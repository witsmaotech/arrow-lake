"""P0-3 (H3): trusted_proxies CIDR support + XFF trust gate tests.

Exact-IP matching is fragile in docker networks (container IPs drift on
recreate). Operators must be able to trust the compose subnet, e.g.
``ARROW_LAKE__RATE_LIMIT__TRUSTED_PROXIES=["172.18.0.0/16"]``.
"""

from __future__ import annotations

from types import SimpleNamespace

from arrow_lake.api.rate_limit import _extract_client_ip


def _request(peer: str, xff: str = "") -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


class TestTrustedProxyCidr:
    def test_cidr_entry_trusts_peer_in_subnet(self) -> None:
        req = _request("172.18.0.5", xff="203.0.113.9, 172.18.0.5")
        assert _extract_client_ip(req, {"172.18.0.0/16"}) == "203.0.113.9"

    def test_exact_ip_entry_still_works(self) -> None:
        req = _request("10.0.0.2", xff="198.51.100.7, 10.0.0.2")
        assert _extract_client_ip(req, {"10.0.0.2"}) == "198.51.100.7"

    def test_peer_outside_cidr_not_trusted(self) -> None:
        # Spoofed XFF must be ignored when the peer is not in a trusted range.
        req = _request("192.168.1.9", xff="1.2.3.4")
        assert _extract_client_ip(req, {"172.18.0.0/16"}) == "192.168.1.9"

    def test_mixed_exact_and_cidr_entries(self) -> None:
        req = _request("172.18.0.5", xff="203.0.113.9, 172.18.0.5")
        assert _extract_client_ip(req, {"10.0.0.2", "172.18.0.0/16"}) == "203.0.113.9"

    def test_invalid_cidr_entry_is_ignored_not_fatal(self) -> None:
        req = _request("172.18.0.5", xff="203.0.113.9, 172.18.0.5")
        # A typo'd entry must neither crash nor widen trust for other peers.
        assert _extract_client_ip(req, {"not-a-cidr"}) == "172.18.0.5"
        assert _extract_client_ip(req, {"not-a-cidr", "172.18.0.0/16"}) == "203.0.113.9"

    def test_xff_walk_skips_trusted_cidr_proxies(self) -> None:
        # Multiple proxy hops: skip every in-subnet hop, stop at real client.
        req = _request("172.18.0.9", xff="203.0.113.9, 172.18.0.5, 172.18.0.9")
        assert _extract_client_ip(req, {"172.18.0.0/16"}) == "203.0.113.9"

    def test_no_xff_returns_peer(self) -> None:
        req = _request("172.18.0.5")
        assert _extract_client_ip(req, {"172.18.0.0/16"}) == "172.18.0.5"

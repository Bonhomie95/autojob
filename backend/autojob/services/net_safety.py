"""
Guard against outbound connections to internal/private network targets.

SMTP and IMAP hosts are user-supplied (Settings → Credentials). Without this,
any registered user could point either at an internal address — a cloud
metadata endpoint, another service on the server's own network — and use
connect-success/refused/timeout differences as a reconnaissance oracle against
infrastructure they have no business probing. Checked at connect time, not
just when the setting is saved, since DNS can repoint a hostname later
(a public IP at save time, a private one by the time we actually connect).
"""

from __future__ import annotations

import ipaddress
import socket


class UnsafeHostError(Exception):
    pass


def assert_safe_host(host: str) -> None:
    """Raise UnsafeHostError if ``host`` resolves to a non-public address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeHostError(f"could not resolve host: {host}") from exc

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise UnsafeHostError(f"{host} resolves to a non-public address ({ip})")

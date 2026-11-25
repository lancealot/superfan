"""Minimal systemd notification (readiness and watchdog pings), no dependencies."""

from __future__ import annotations

import os
import socket


def notify(message: str) -> bool:
    """Send `message` to systemd. Does nothing, and returns False, outside systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError:
        return False
    return True

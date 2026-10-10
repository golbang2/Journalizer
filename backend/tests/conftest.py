"""Test-only workaround for intermittent WinError 10014 on some Windows machines.

Every asyncio event loop (and so every TestClient) creates a loopback socket pair.
On some setups creating it occasionally fails with WSAEFAULT (10014) for no reason
related to the code under test; retrying succeeds.
"""
import socket
import sys
import time

if sys.platform == "win32":
    _socketpair = socket.socketpair

    def _retrying_socketpair(*args, **kwargs):
        for attempt in range(5):
            try:
                return _socketpair(*args, **kwargs)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 10014 or attempt == 4:
                    raise
                time.sleep(0.05)

    socket.socketpair = _retrying_socketpair

# Tests never call the Claude API; the AI judge is exercised with a stub where needed.
import os

os.environ["AI_JUDGE"] = "off"

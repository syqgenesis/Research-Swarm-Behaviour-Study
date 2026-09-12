"""Test package init — installs a permanent network lockdown for the whole suite.

No test in this project may open a real socket. Every live API call goes
through swarm.client.call_model; tests patch that. This guard makes it a hard
requirement rather than a convention: a test that forgets to patch and falls
through to a real socket fails immediately and loudly instead of silently
spending money.

Zero dependencies on anything else in the test package — it must be safe to
import before any other test module.
"""
import socket


class _RealNetworkBlocked(RuntimeError):
    pass


def _blocked_socket(*args, **kwargs):
    raise _RealNetworkBlocked(
        "a test tried to open a real socket — patch swarm.client.call_model "
        "instead. No test may make a live API call."
    )


socket.socket = _blocked_socket

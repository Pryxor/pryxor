"""
Pytest bootstrap + isolation helpers.

Goals:

1. Make the project root importable so `tests/` can `import pryxor_*`.
2. Guarantee that NO background worker outlives the test that created it:
   a leaked worker keeps polling SQLite, refreshing the process-global
   Prometheus gauges, and can fire real outbound HTTP (notifier), which
   corrupts unrelated tests and slows the whole run.
3. Guarantee that NO accidental outbound network call happens from a test
   unless that test explicitly mocks it.

How isolation is achieved:

* ``PRYXOR_DISPATCH_WORKER=0`` is set at import time — *before* any engine is
  constructed — so ``PolicyEngine`` never starts its background dispatch worker
  during tests, even if a test reloads ``pryxor_engine`` (which would otherwise
  bypass any monkeypatch of the class).
* ``_start_dispatch_worker`` is additionally neutralised as a belt-and-braces
  guard for engines created via a reloaded module.
* ``requests`` and ``httpx`` are blocked at the transport level so a leftover
  code path cannot reach the network; tests that need HTTP patch these
  explicitly (their patch takes precedence because it replaces the attribute).
"""

import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The Python client, adapters, and MCP integration live in the `pryxor`
# package under sdk/src. Make it importable so tests can `import pryxor`
# without requiring an install.
SDK_SRC = ROOT / "sdk" / "src"
if SDK_SRC.is_dir() and str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))

# --- 1. Disable the background worker before any import --------------------
# Must happen at module import (before pytest imports the app modules / before
# any test constructs a PolicyEngine).
os.environ.setdefault("PRYXOR_DISPATCH_WORKER", "0")

# Engines created during a test, so we can stop their workers in teardown.
_ENGINES: list = []


@pytest.fixture(autouse=True)
def _isolate_engine_workers(monkeypatch):
    """
    Track every PolicyEngine created during the test and make sure no worker
    leaks. Also neutralise the background gauge refresh, which writes to the
    process-global Prometheus gauges and would race with tests that set them
    (e.g. test_metrics::test_gauges_can_be_set).
    """
    _ENGINES.clear()

    try:
        import pryxor_engine
    except Exception:
        yield
        return

    original_init = pryxor_engine.PolicyEngine.__init__

    def tracked_init(self, *args, **kwargs):
        # Force the worker off in tests regardless of how the engine was built.
        kwargs["enable_dispatch_worker"] = False
        original_init(self, *args, **kwargs)
        _ENGINES.append(self)

    monkeypatch.setattr(pryxor_engine.PolicyEngine, "__init__", tracked_init)

    # Belt-and-braces: if a test reloaded the module, __init__ patching above
    # won't apply to the new class. Neutralise the start method the new class
    # will call, so no worker can start under any code path.
    monkeypatch.setattr(
        pryxor_engine.PolicyEngine, "_start_dispatch_worker", lambda self: None
    )

    # Neutralise the background gauge refresh during tests.
    monkeypatch.setattr(pryxor_engine.PolicyEngine, "_refresh_gauges", lambda self: None)

    yield

    for eng in _ENGINES:
        try:
            eng.stop_dispatch_worker()
        except Exception:
            pass
    _ENGINES.clear()

    # Reset shared gauges so a later test starts from a known state.
    try:
        import pryxor_metrics as metrics

        metrics.set_holds_pending(0)
        metrics.set_outbox_pending(0)
        metrics.set_notifications_pending(0)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """
    Fail fast if a test tries to make a real outbound HTTP call.

    Tests that legitimately exercise HTTP (or the notifiers) replace these
    attributes themselves *after* this fixture runs, so their patch wins.
    Any other outbound call raises instead of hitting the network / DNS.
    """
    try:
        import requests
    except Exception:
        requests = None

    if requests is not None:

        def _no_network(*_args, **_kwargs):
            raise RuntimeError(
                "Outbound network call blocked in tests. "
                "Mock requests.post/get (or httpx) explicitly if the test needs HTTP."
            )

        monkeypatch.setattr(requests, "post", _no_network, raising=False)
        monkeypatch.setattr(requests, "get", _no_network, raising=False)
        monkeypatch.setattr(requests, "request", _no_network, raising=False)
        # Guard the low-level entry used by some libraries.
        monkeypatch.setattr(requests, "Session", _no_network, raising=False)

    try:
        import httpx

        class _BlockedClient:
            def __init__(self, *_a, **_k):
                raise RuntimeError("Outbound httpx.Client blocked in tests.")

        monkeypatch.setattr(httpx, "Client", _BlockedClient, raising=False)
    except Exception:
        pass

    yield

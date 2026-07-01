"""Test guardrails for the root suite.

GUARDRAIL (#217): ``provision()`` now installs a periodic drift-watcher launch
agent by default (``ProvisionConfig.drift_watcher_enabled`` defaults True). The
real ``DriftWatcherGateway`` would write to ``~/Library/LaunchAgents`` and shell
out to ``launchctl``. This autouse fixture neutralizes the gateway's DEFAULT
install/uninstall/check callables so no test ever touches the live system.
Tests that inject their own gateway are unaffected (they pass explicit
callables, so these defaults are never used).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_provision_engine():
    # Load under the SAME module name the tests cache it as ("provision_engine")
    # so we patch the exact class instance provision() will construct.
    import sys

    mod_name = "provision_engine"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = Path(__file__).resolve().parents[1] / "scripts" / "provision_engine.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _neutralize_real_drift_watcher(monkeypatch):
    try:
        mod = _load_provision_engine()
    except Exception:  # noqa: BLE001 — if it can't load, tests will surface it themselves
        return
    gw = getattr(mod, "DriftWatcherGateway", None)
    if gw is None:
        return
    monkeypatch.setattr(gw, "_default_check", classmethod(lambda cls: False))
    monkeypatch.setattr(gw, "_default_install", classmethod(lambda cls: None))
    monkeypatch.setattr(gw, "_default_uninstall", classmethod(lambda cls: None))

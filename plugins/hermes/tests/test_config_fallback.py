"""Regression tests: the plugin package must survive a hermes install whose
hermes_cli config-import chain is broken.

Incident background (2026-11-25): hermes-agent updated its source in place
(adds top-level hermes_yaml.py), but stale editable-install finders never
mapped it. Every import of hermes_cli.config then failed with
ModuleNotFoundError: No module named 'hermes_yaml' — and because this plugin's
config module imported hermes_cli.config at top level, the ENTIRE provider
(plugin entry point, GT_* tools) died silently at plugin load across every
Python surface. These tests pin the degradation contract: a broken hermes_cli
import chain must fall back to reading config.yaml directly, and the provider
must still register its tools.

Scope: only hermes_cli/hermes_yaml-adjacent imports are hardened. The plugin's
imports of agent.memory_provider / tools.registry (hermes constants are the
only agent-side imports they need) are left alone deliberately: if those are
unimportable, the host hermes process itself is broken and no plugin can run
in it, so plugin-side degradation cannot help.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from golden_thread_hermes.memory.config import (
    _cfg_get,
    _read_config_yaml,
)

SRC = Path(__file__).resolve().parent.parent / "src"


def test_cfg_get_walks_nested_path():
    config = {"plugins": {"golden-thread": {"vault_path": "/vp"}}}
    assert _cfg_get(config, "plugins", "golden-thread") == {"vault_path": "/vp"}
    assert _cfg_get(config, "plugins", "golden-thread", default={}) == {"vault_path": "/vp"}
    assert _cfg_get(config, "plugins", "missing", default="x") == "x"
    assert _cfg_get(None, "plugins", default="d") == "d"


def test_read_config_yaml_missing_file_returns_empty(tmp_path):
    assert _read_config_yaml(tmp_path) == {}


def test_read_config_yaml_reads_nested_block(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  golden-thread:\n    vault_path: /vp\n    active_project: proj-x\n"
    )
    data = _read_config_yaml(tmp_path)
    assert data["plugins"]["golden-thread"]["vault_path"] == "/vp"


def test_fallback_hermes_home_env_then_default(monkeypatch):
    import golden_thread_hermes.memory.config as cfgmod

    monkeypatch.setattr(cfgmod, "get_hermes_home", None)
    monkeypatch.setenv("HERMES_HOME", "/tmp/hh")
    assert cfgmod._hermes_home() == Path("/tmp/hh")
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert cfgmod._hermes_home() == Path.home() / ".hermes"


def test_broken_hermes_import_degrades_to_yaml_fallback(tmp_path):
    """Subprocess regression for the full degraded path: poison hermes_cli and
    hermes_constants (the ModuleNotFoundError failure class), then load() must
    still resolve vault config from config.yaml and save() must still write
    it — no import-chain exception allowed."""
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  golden-thread:\n"
        "    vault_path: /tmp/demo-vault\n"
        "    active_project: proj-x\n"
        "    ui_pane_auto: false\n"
    )
    script = textwrap.dedent(
        """
        import os
        import sys

        sys.modules["hermes_cli"] = None           # -> ImportError on import
        sys.modules["hermes_cli.config"] = None
        sys.path.insert(0, {src})

        from golden_thread_hermes.memory.config import GoldenThreadConfig

        cfg = GoldenThreadConfig.load()
        assert str(cfg.vault_path) == "/tmp/demo-vault"
        assert cfg.active_project == "proj-x"
        assert cfg.ui_pane_auto is False
        assert cfg.core_rules_max_lines == 200  # untouched default

        cfg.active_project = "proj-y"
        cfg.save()

        import yaml
        with open(os.environ["HERMES_HOME"] + "/config.yaml") as f:
            saved = yaml.safe_load(f)
        assert saved["plugins"]["golden-thread"]["active_project"] == "proj-y"
        assert saved["plugins"]["golden-thread"]["ui_pane_auto"] is False
        print("SUBPROCESS-OK")
        """
    ).format(src=repr(str(SRC)))
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SUBPROCESS-OK" in proc.stdout

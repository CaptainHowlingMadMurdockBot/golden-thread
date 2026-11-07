import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent / 'src'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture(autouse=True)
def hermetic_config_save(tmp_path, monkeypatch):
    """GoldenThreadConfig.save() rewrites the LIVE Hermes config.yaml. A Nov-2026
    pytest run pumped pytest scratch vault paths into the live config through
    gt_open/gt_decide calls made OUTSIDE the per-test save patch; every save()
    also yaml.dumps the whole live config. Redirect all saves to the test tmp
    dir so tests can never touch production state."""
    import yaml
    from golden_thread_hermes.memory.config import GoldenThreadConfig

    def fake_save(self):
        cfg = {"plugins": {"golden-thread": {
            "vault_path": str(self.vault_path),
            "active_project": self.active_project,
            "core_rules_max_lines": self.core_rules_max_lines,
            "project_context_max_lines": self.project_context_max_lines,
        }}}
        with open(tmp_path / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, default_flow_style=False)

    monkeypatch.setattr(GoldenThreadConfig, 'save', fake_save)

@pytest.fixture
def tmp_vault(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / 'Core.md').write_text("# Core Rules\n- Body trust is foundational.\n")
    (vault / 'Inbox.md').write_text("# Inbox\n")
    projects = vault / 'Projects' / 'demo'
    projects.mkdir(parents=True)
    (projects / 'idea.md').write_text("---\nstatus: active\n---\n# Demo project\n")
    (projects / 'CONVENTIONS.md').write_text("# Conventions\n- Use English.\n")
    return vault

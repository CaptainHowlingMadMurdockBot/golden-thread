import asyncio
import json
from unittest.mock import patch

from golden_thread_hermes.memory import vault as vault_module
from golden_thread_hermes.memory.vault import Vault
from golden_thread_hermes.memory.provider import GoldenThreadMemoryProvider
from golden_thread_hermes.memory.config import GoldenThreadConfig


def _run(coro):
    return asyncio.run(coro)


def test_recall_ranks_matching_page_top(tmp_vault):
    if vault_module._find_recall_script() is None:
        # Skip cleanly in an environment with neither an installed hooks copy
        # nor the repo-bundled golden-thread-recall release.
        return
    target_dir = tmp_vault / 'Projects' / 'demo' / 'Log'
    target_dir.mkdir(parents=True, exist_ok=True)
    log = target_dir / '2026-11-15.md'
    log.write_text(
        'quixotic wombat discountcode camp week seeded 58 users\n' * 3
    )
    v = Vault(str(tmp_vault))
    result = _run(v.recall('quixotic wombat discountcode'))
    assert result['status'] == 'ok'
    assert result['results'], 'recall returned no results'
    assert result['results'][0].endswith('Projects/demo/Log/2026-11-15.md')
    assert result['hits'][0]['method'] == 'bm25'


def test_recall_empty_question(tmp_vault):
    v = Vault(str(tmp_vault))
    result = _run(v.recall('   '))
    assert result['status'] == 'error'
    assert 'non-empty' in result['detail']


def test_recall_missing_script_reports_error(tmp_vault, monkeypatch):
    monkeypatch.setattr(vault_module, '_find_recall_script', lambda: None)
    v = Vault(str(tmp_vault))
    result = _run(v.recall('anything at all'))
    assert result['status'] == 'error'
    assert 'not found' in result['detail']


def test_recall_through_dispatch(tmp_vault):
    """The tool surface: gt_recall must actually be registered end-to-end."""
    if vault_module._find_recall_script() is None:
        return
    target_dir = tmp_vault / 'Projects' / 'demo' / 'Log'
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / '2026-11-15.md').write_text(
        'quixotic wombat discountcode camp week seeded 58 users\n' * 3
    )
    p = GoldenThreadMemoryProvider()
    cfg = GoldenThreadConfig(str(tmp_vault))
    with patch.object(GoldenThreadConfig, 'load', return_value=cfg):
        with patch.object(GoldenThreadConfig, 'save'):
            p.initialize('test-session')
    res = json.loads(p.handle_tool_call('gt_recall', {'question': 'quixotic wombat discountcode'}))
    assert res['status'] == 'ok'
    assert res['results'], 'expected the seeded Projects/ page in the corpus'

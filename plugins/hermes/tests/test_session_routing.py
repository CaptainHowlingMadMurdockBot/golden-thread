"""Per-session active-project routing (2026-11-18 fix for the cqnw misfile).

ONE provider instance serves every Hermes session — desktop windows, gateway
chats, /new and compression rotations. Before the fix it bound a single global
config.active_project ("whoever gt_open'ed last wins"), so a session that
never ran gt_open inherited the last opener's project and every auto-writer
(per-turn paste, pre-compression checkpoint, session-end line) misfiled into
it: a Daggerheart Q&A session landed in cqnw/Log/2026-11-19.md.

Contract under test:
- gt_open / gt_create claim the CALLING session (scope "session"); other
  sessions stay unclaimed.
- Auto-writers require an explicit session claim — no claim, no writes.
- Unbound sessions are refused for write-commands (fresh-start 2026-11-20):
  nothing reads or writes config.active_project; sessions bind themselves.
- gt_close releases only the calling session's claim.
"""
import asyncio
import json
from unittest.mock import patch

from golden_thread_hermes.memory.config import GoldenThreadConfig
from golden_thread_hermes.memory.project import ProjectManager
from golden_thread_hermes.memory.session_claims import SessionClaims
from golden_thread_hermes.memory.vault import Vault

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from test_provider import _latest_log_text, make_provider


def _log_dir(vault, slug):
    return Path(vault) / 'Projects' / slug / 'Log'


def test_gt_open_claims_only_the_calling_session(tmp_vault):
    p = make_provider(tmp_vault, session_id='sess-A')
    res = json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    assert res['scope'] == 'session'
    # claims are shared via the vault file, visible to any other instance
    other = SessionClaims(tmp_vault)
    assert other.get('sess-A') == 'demo'
    assert other.get('sess-B') == ''
    # fresh-start: opening never writes the global; sessions don't follow each
    # other through any shared key
    assert p.config.active_project == ''


def test_new_session_with_no_claim_inherits_nothing(tmp_vault):
    """The cqnw regression: /new (or a gateway pickup) on a provider whose
    predecessor opened cqnw must NOT log the new session's turns there."""
    p = make_provider(tmp_vault, session_id='sess-A')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.on_session_switch('sess-B')
    p.sync_turn('user', 'daggerheart rules question', session_id='sess-B')
    p.on_session_end([{'role': 'user', 'content': 'h'}])
    log = _latest_log_text(tmp_vault)
    assert 'daggerheart' not in log
    assert 'session end' not in log


def test_claimed_session_routes_after_switch_back(tmp_vault):
    p = make_provider(tmp_vault, session_id='sess-A')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.on_session_switch('sess-B')  # unclaimed visitor changes nothing
    p.on_session_switch('sess-A')
    p.sync_turn('user', 'welcome back turn', session_id='sess-A')
    assert 'welcome back turn' in _latest_log_text(tmp_vault)


def test_unbound_session_refuses_write_commands(tmp_vault):
    """Fresh-start contract (2026-11-20): a session that never claimed a
    project gets NO fallback. Even a stale legacy config.active_project seed
    must not help: write-commands are refused, auto-writers stay silent."""
    p = make_provider(tmp_vault, session_id='fresh-1')
    p.config.active_project = 'demo'  # stale legacy seed must not route
    p.sync_turn('user', 'unclaimed session noise', session_id='fresh-1')
    assert 'unclaimed session noise' not in _latest_log_text(tmp_vault)
    res = json.loads(p.handle_tool_call('gt_work', {'summary': 'one-shot pin'}))
    assert res['status'] == 'unbound'
    assert 'gt_open' in res['message']
    assert 'one-shot pin' not in _latest_log_text(tmp_vault)
    # and the stale seed is not consumed: nothing logged anywhere
    assert 'demo' not in _latest_log_text(tmp_vault)


def test_pre_compress_unclaimed_reports_none_and_hint(tmp_vault):
    p = make_provider(tmp_vault, session_id='fresh-1')
    p.handle_tool_call('gt_list', {})  # non-empty session events
    text = p.on_pre_compress([{'role': 'user', 'content': 'x'}])
    assert 'demo' not in text
    assert 'gt_open' in text  # fallback hint, not a foreign project
    assert 'pre-compression' not in _latest_log_text(tmp_vault)


def test_gt_close_releases_only_the_calling_session(tmp_vault):
    # second project so both sessions can claim different slugs
    gdir = tmp_vault / 'Projects' / 'gamma'
    (gdir / 'Log').mkdir(parents=True)
    (gdir / 'idea.md').write_text('# Gamma\n')
    p = make_provider(tmp_vault, session_id='sess-A')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.on_session_switch('sess-B')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'gamma'}))
    res = json.loads(p.handle_tool_call('gt_close', {}))
    assert res['scope'] == 'session'
    assert res['was_active'] == 'gamma'
    gamma_log = ''.join(
        f.read_text() for f in sorted(_log_dir(tmp_vault, 'gamma').iterdir()))
    assert 'Session closed for gamma' in gamma_log
    claims = SessionClaims(tmp_vault)
    assert claims.get('sess-B') == ''
    assert claims.get('sess-A') == 'demo'
    # the other session keeps working against its own claim
    p.on_session_switch('sess-A')
    p.sync_turn('user', 'still demo', session_id='sess-A')
    assert 'still demo' in _latest_log_text(tmp_vault)


def test_legacy_close_without_session_clears_global(tmp_vault):
    """CLI scripts call ProjectManager.close_command directly (no claims) —
    must keep the old global-clear behavior."""
    cfg = GoldenThreadConfig(tmp_vault, active_project='demo')
    res = asyncio.run(ProjectManager(Vault(tmp_vault), 'demo').close_command(cfg))
    assert res['scope'] == 'global-fallback'
    assert res['was_active'] == 'demo'
    assert cfg.active_project == ''


def test_claims_file_shared_between_instances_and_prunes(tmp_vault):
    a = SessionClaims(tmp_vault)
    a.set('s1', 'demo')
    assert SessionClaims(tmp_vault).get('s1') == 'demo'
    path = tmp_vault / '.gt' / 'hermes-session-claims.json'
    data = json.loads(path.read_text())
    data['sessions']['ancient'] = {'project': 'demo',
                                   'updated_at': '2020-01-01T00:00:00+00:00'}
    path.write_text(json.dumps(data))
    b = SessionClaims(tmp_vault)
    b.set('s2', 'demo')  # set() prunes stale entries as a side effect
    assert b.get('ancient') == ''
    assert 'ancient' not in json.loads(path.read_text())['sessions']
    assert b.clear('s2') == 'demo'
    assert b.get('s2') == ''


def test_on_session_switch_rotates_provider_state(tmp_vault):
    p = make_provider(tmp_vault, session_id='first')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.on_session_switch('second')
    assert p._session_id == 'second'
    # rotated session is unclaimed: no auto writes despite demo claim earlier
    p.sync_turn('user', 'post-switch turn', session_id='first')  # stale id
    assert 'post-switch turn' not in _latest_log_text(tmp_vault)
    # unclaimed rotation sees NO fallback seed (fresh-start): hint scope only
    slug, scope = p._resolve_active('second')
    assert scope == 'fallback' and slug == ''
    # compression-style rotation carries the claim to the new id
    p.on_session_switch('third', parent_session_id='first')
    claims = SessionClaims(tmp_vault)
    assert claims.get('third') == 'demo' and claims.get('first') == ''
    p.sync_turn('user', 'continuation turn', session_id='third')
    assert 'continuation turn' in _latest_log_text(tmp_vault)


def test_gt_create_claims_the_calling_session(tmp_vault):
    p = make_provider(tmp_vault, session_id='creator')
    res = json.loads(p.handle_tool_call(
        'gt_create', {'name': 'gamma2', 'idea': 'created by test'}))
    assert res['scope'] == 'session'
    assert SessionClaims(tmp_vault).get('creator') == 'gamma2'

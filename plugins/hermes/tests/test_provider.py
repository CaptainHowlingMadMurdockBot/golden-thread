import json
from unittest.mock import patch

import asyncio
import pytest
import json
from golden_thread_hermes.memory.provider import GoldenThreadMemoryProvider
from golden_thread_hermes.memory.config import GoldenThreadConfig
from golden_thread_hermes.memory.project import ProjectManager
from golden_thread_hermes.memory.vault import Vault

def make_provider(vault, session_id='test-session'):
    p = GoldenThreadMemoryProvider()
    cfg = GoldenThreadConfig(vault)
    with patch.object(GoldenThreadConfig, 'load', return_value=cfg):
        with patch.object(GoldenThreadConfig, 'save'):
            p.initialize(session_id)
    return p

def test_provider_lifecycle(tmp_vault):
    p = make_provider(tmp_vault)
    assert p.is_available()

def test_provider_schemas(tmp_vault):
    p = make_provider(tmp_vault)
    schemas = p.get_tool_schemas()
    names = sorted([s['name'] for s in schemas])
    assert len(names) == 53
    # All canonical Claude Code commands are present
    canonical = [
        'gt_allin', 'gt_allin_commit', 'gt_brief', 'gt_calibrate', 'gt_close',
        'gt_complete', 'gt_context', 'gt_create', 'gt_digest', 'gt_doctor',
        'gt_farm', 'gt_forget', 'gt_grant', 'gt_handle', 'gt_help',
        'gt_implement', 'gt_ingest', 'gt_init', 'gt_lint', 'gt_list',
        'gt_minimize', 'gt_open', 'gt_optimize', 'gt_plan', 'gt_promote',
        'gt_query', 'gt_recall', 'gt_refresh', 'gt_review', 'gt_route',
        'gt_runbook_lint', 'gt_scan', 'gt_settings', 'gt_setup', 'gt_sync',
        'gt_upgrade', 'gt_validate', 'gt_validation', 'gt_watch', 'gt_work',
    ]
    for name in canonical:
        assert name in names, f"missing canonical command {name}"

def test_provider_system_prompt(tmp_vault):
    p = make_provider(tmp_vault)
    # The provider always injects Core rules when a Core.md exists
    block = p.system_prompt_block()
    assert 'Body trust' in block
    # After opening a project, the prefetch returns project context
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    prefetch = p.prefetch("status")
    assert 'demo' in prefetch


def test_handle_tool_call_inside_running_event_loop(tmp_vault):
    """Regression (2026-11-07): Hermes' desktop/gateway executor calls the sync
    provider handle_tool_call INSIDE a running event loop, where asyncio.run()
    raises 'RuntimeError: cannot be called from a running event loop'. The
    provider must bridge to its own loop instead of failing every gt_* call."""
    import asyncio
    p = make_provider(tmp_vault)

    async def drive():
        return p.handle_tool_call('gt_list', {})

    res = json.loads(asyncio.run(drive()))
    assert 'error' not in res, f"gt_list failed inside running loop: {res}"
    assert isinstance(res.get('projects'), list)


def _latest_log_text(vault, slug='demo'):
    log_dir = vault / 'Projects' / slug / 'Log'
    if not log_dir.exists():
        return ''
    return ''.join(f.read_text() for f in sorted(log_dir.iterdir()))


def test_on_pre_compress_writes_checkpoint_and_returns_text(tmp_vault):
    """gt-minimize parity: compression must leave a vault trail and get a
    checkpoint block for the post-compression summary."""
    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.handle_tool_call('gt_work', {'summary': 'did the thing'})
    messages = [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}]
    text = p.on_pre_compress(messages)
    assert '[GT checkpoint]' in text
    assert 'demo' in text
    assert 'gt_work' in text
    log = _latest_log_text(tmp_vault)
    assert 'pre-compression checkpoint' in log
    # dedup: same turn count again -> no second checkpoint line
    p.on_pre_compress(messages)
    assert _latest_log_text(tmp_vault).count('pre-compression checkpoint') == 1


def test_on_pre_compress_no_active_project_returns_empty(tmp_vault):
    p = make_provider(tmp_vault)
    assert p.on_pre_compress([{'role': 'user', 'content': 'hi'}]) == ''
    assert 'pre-compression' not in _latest_log_text(tmp_vault)


def test_on_session_end_writes_close_line(tmp_vault):
    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.on_session_end([{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}])
    log = _latest_log_text(tmp_vault)
    assert 'session end' in log
    assert 'gt_open' in log


def test_cron_env_marker_gates_auto_log(tmp_vault, monkeypatch):
    """Regression (2026-11-08): with a saved active project, every cron job's
    turn text and "session end" checkpoint landed in Projects/<slug>/Log/
    (~170 noise lines vs 11 real entries in one day). Cron-marked
    (os.environ fallback) sessions must not auto-log."""
    monkeypatch.setenv('HERMES_CRON_SESSION', '1')
    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.sync_turn('user', 'cron digest text', session_id='s')
    p.on_session_end([])
    log = _latest_log_text(tmp_vault)
    assert 'cron digest text' not in log
    assert 'session end' not in log


def test_single_query_marker_gates_auto_log(tmp_vault, monkeypatch):
    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    monkeypatch.setenv('HERMES_SINGLE_QUERY_SESSION', '1')
    assert p._unattended_session()
    p.sync_turn('user', 'one-shot text', session_id='s')
    assert 'one-shot text' not in _latest_log_text(tmp_vault)


def test_gateway_cron_contextvar_gates_auto_log(tmp_vault):
    """The cron worker marks jobs with the gateway session ContextVar; the
    same marker the LSP-disable check reads must gate the capture hooks."""
    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    from gateway.session_context import _VAR_MAP
    token = _VAR_MAP['HERMES_CRON_SESSION'].set('1')
    try:
        assert p._unattended_session()
        p.sync_turn('user', 'worker turn text', session_id='s')
        p.on_session_end([])
        log = _latest_log_text(tmp_vault)
        assert 'worker turn text' not in log
        assert 'session end' not in log
    finally:
        _VAR_MAP['HERMES_CRON_SESSION'].reset(token)


def test_interactive_auto_log_still_works(tmp_vault, monkeypatch):
    monkeypatch.delenv('HERMES_CRON_SESSION', raising=False)
    monkeypatch.delenv('HERMES_SINGLE_QUERY_SESSION', raising=False)
    # Since per-session claims (2026-11-18), the capture writer routes by the
    # session_id that holds the claim, so initialize and sync_turn must agree.
    p = make_provider(tmp_vault, session_id='s')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    p.sync_turn('user', 'interactive reply', session_id='s')
    assert 'interactive reply' in _latest_log_text(tmp_vault)


def test_hooks_inside_running_event_loop(tmp_vault):
    """Gateway/desktop executes providers inside a live loop (pitfall 20) —
    the capture hooks run on that path too and must never raise."""
    import asyncio

    p = make_provider(tmp_vault)
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))

    async def drive():
        a = p.on_pre_compress([{'role': 'user', 'content': 'z'}])
        p.on_session_end([])
        return a

    text = asyncio.run(drive())  # raises if a hook blows up on a live loop
    assert '[GT checkpoint]' in text


def test_session_events_ring_bounds(tmp_vault):
    p = make_provider(tmp_vault)
    for _ in range(60):
        p.handle_tool_call('gt_list', {})
    # events are bounded so long sessions can't balloon the hook block
    assert len(p._session_events) <= 40

def test_non_gt_tools_not_recorded(tmp_vault):
    p = make_provider(tmp_vault)
    p.handle_tool_call('gt_list', {})
    p._record_tool_event('some_other_tool', {'x': 1})
    assert all(e.startswith('gt_') for e in p._session_events)


# ---------------------------------------------------------------------------
# gt_minimize: measure-and-triage flow (Claude Code gt-minimize parity)
# ---------------------------------------------------------------------------

import sqlite3 as _sqlite3


def _make_state_db(path, rows):
    con = _sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, message_count INTEGER,"
        " tool_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,"
        " cache_read_tokens INTEGER, cache_write_tokens INTEGER, started_at REAL,"
        " last_activity_at REAL, archived INTEGER)"
    )
    for i, r in enumerate(rows):
        con.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["messages"], r["tools"], r["tin"], r["tout"],
             r["cr"], r["cw"], 1.0, r.get("la", 1.0 + i), 0),
        )
    con.commit()
    con.close()


def _db_rows():
    return [
        {"id": "sess-1", "messages": 12, "tools": 4, "tin": 50_000, "tout": 900,
         "cr": 500_000, "cw": 20_000},
        {"id": "sess-2", "messages": 99, "tools": 30, "tin": 900_000, "tout": 5_000,
         "cr": 9_000_000, "cw": 80_000},
    ]


def test_minimize_measures_session_by_id(tmp_vault):
    db = tmp_vault / 'state.db'
    _make_state_db(db, _db_rows())
    pm = ProjectManager(Vault(tmp_vault), 'demo')
    # fixture has no README; one open checkbox is enough to prove counting
    readme = tmp_vault / 'Projects' / 'demo' / 'README.md'
    readme.write_text('# Tasks\n\n- [ ] 73b631 wire hooks (p1, agent)\n')
    res = asyncio.run(pm.minimize_command(session_id='sess-1', db_path=db))
    assert res['status'] == 'ok'
    assert res['session']['session_id'] == 'sess-1'
    assert res['session']['matched_by'] == 'id'
    assert res['session']['messages'] == 12
    assert res['session']['cache_reads_per_input'] == 10.0
    assert res['open_tasks'] >= 1  # canonical task checkboxes in demo README
    # prep line landed in the project Log
    log_file = tmp_vault / 'Projects' / 'demo' / 'Log'
    text = ''.join(f.read_text() for f in sorted(log_file.iterdir()))
    assert 'minimize prep' in text


def test_minimize_falls_back_to_newest_session(tmp_vault):
    db = tmp_vault / 'state.db'
    _make_state_db(db, _db_rows())
    pm = ProjectManager(Vault(tmp_vault), 'demo')
    res = asyncio.run(pm.minimize_command(session_id='nope', db_path=db))
    assert res['session']['matched_by'] == 'newest'
    assert res['session']['session_id'] == 'sess-2'


def test_minimize_reports_missing_db_gracefully(tmp_vault):
    pm = ProjectManager(Vault(tmp_vault), 'demo')
    res = asyncio.run(pm.minimize_command(db_path=tmp_vault / 'missing.db'))
    assert res['status'] == 'ok'
    assert 'no runtime state db' in res['session']['error']


def test_minimize_requires_active_project(tmp_vault):
    pm = ProjectManager(Vault(tmp_vault), '')
    with pytest.raises(RuntimeError):
        asyncio.run(pm.minimize_command(db_path=tmp_vault / 'missing.db'))


def test_gt_minimize_tool_plumbs_session_id(tmp_vault):
    """initialize(session_id) must reach the measure step through the tool call."""
    p = make_provider(tmp_vault)
    p._session_id = 'sess-9'  # what initialize() stores at first turn
    # hermetic: point runtime db probe at a location that cannot exist as a db
    type(p.project).runtime_db_path = staticmethod(lambda: tmp_vault / 'no.db')
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    res = json.loads(p.handle_tool_call('gt_minimize', {}))
    assert res['session']['session_id'] == 'sess-9'
    assert 'no runtime state db' in res['session']['error']

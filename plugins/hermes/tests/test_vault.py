from golden_thread_hermes.memory.vault import Vault

def test_vault_core_read(tmp_vault):
    v = Vault(tmp_vault)
    assert 'Body trust' in v.read_core()
    assert v.list_projects() == ['demo']

def test_slugify(tmp_vault):
    v = Vault(tmp_vault)
    assert v.slugify('Hello World!') == 'hello-world'

def test_list_projects_includes_subprojects(tmp_vault):
    v = Vault(tmp_vault)
    child = tmp_vault / 'Projects' / 'demo' / 'subdemo'
    child.mkdir()
    (child / 'idea.md').write_text('# Sub\n')
    (tmp_vault / 'Projects' / 'demo' / 'junkdir').mkdir()  # no README/idea.md -> not a project
    assert v.list_projects() == ['demo', 'demo/subdemo']

# --- engine-script resolution (cold-start / fresh install) ---

def test_engine_script_env_wins(tmp_vault, monkeypatch):
    import golden_thread_hermes.memory.vault as vault_module
    fake = tmp_vault / 'fakehooks'
    fake.mkdir()
    (fake / 'gt_write_queue.py').write_text('# stub\n')
    monkeypatch.setenv('GT_HOOKS_DIR', str(fake))
    assert vault_module._resolve_engine_script('gt_write_queue.py') == fake / 'gt_write_queue.py'

def test_engine_script_bundled_fallback(tmp_vault, monkeypatch):
    import golden_thread_hermes.memory.vault as vault_module
    missing = tmp_vault / 'no-hooks-here'
    monkeypatch.setenv('GT_HOOKS_DIR', str(missing))
    resolved = vault_module._resolve_engine_script('gt_write_queue.py')
    # falls through to the repo-bundled newest release
    assert resolved.exists()
    assert 'golden-thread-plugin/golden-thread/' in str(resolved)

def test_engine_script_brother_fallback_for_broker(tmp_vault, monkeypatch):
    import golden_thread_hermes.memory.vault as vault_module
    monkeypatch.setenv('GT_HOOKS_DIR', str(tmp_vault / 'empty-hooks'))
    resolved = vault_module._resolve_engine_script('gt_broker.py')
    assert resolved.exists()

def test_engine_script_nothing_found_reports_hooks_path(tmp_vault, monkeypatch):
    import golden_thread_hermes.memory.vault as vault_module
    hooks = tmp_vault / 'empty-hooks'
    monkeypatch.setenv('GT_HOOKS_DIR', str(hooks))
    monkeypatch.setattr(vault_module, '_newest_bundled_release', lambda sub: None)
    resolved = vault_module._resolve_engine_script('gt_write_queue.py')
    assert resolved.parent == hooks
    assert not resolved.exists()

def test_default_hooks_dir_is_home_relative(monkeypatch, tmp_path):
    import golden_thread_hermes.memory.vault as vault_module
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('GT_HOOKS_DIR', raising=False)
    assert vault_module._default_hooks_dir() == tmp_path / '.claude' / 'golden-thread' / 'hooks'

# --- core-rules tier injection (hooks/inject_core_rules.sh / gt_paths parity) ---

import yaml as _yaml
from golden_thread_hermes.memory.vault import Vault


def _write_rule(vault, name, enforcement='reminder', imperative=None,
                body='', extra_fm=''):
    """Synthetic rule file in vault/core-rules. Name comes from the file stem
    (gt_paths.parse_rule semantics). imperative=None exercises the bold-
    fallback: no imperative in frontmatter, picked from **bold** body text."""
    d = vault / 'core-rules'
    d.mkdir(exist_ok=True)
    (d / 'core_rule_priority_model.md').write_text('marker\n')
    lines = ['---', 'metadata:', '  level: core', f'  enforcement: {enforcement}']
    if imperative is not None:
        lines.append(f'imperative: "{imperative}"')
    if extra_fm:
        lines.extend(extra_fm.splitlines())
    (d / f'{name}.md').write_text('\n'.join(lines) + '\n---\n' + body)


def _write_settings(vault, data):
    gt = vault / '.gt'
    gt.mkdir(exist_ok=True)
    (gt / 'settings.yaml').write_text(_yaml.dump(data))


def _tier(vault):
    return vault / 'core-rules'


def test_read_core_rules_orders_validated_first(tmp_vault):
    _write_rule(tmp_vault, 'core_zulu', imperative='Zulu reminder line.')
    _write_rule(tmp_vault, 'core_alpha', enforcement='validated', imperative='Alpha validated line.')
    v = Vault(tmp_vault)
    out = v.read_core_rules()
    lines = out.splitlines()
    assert lines[0] == 'CORE RULES (always on, every turn, every project — see core-rules):'
    assert lines[1] == '1. Alpha validated line.'
    assert lines[2] == '2. Zulu reminder line.'

def test_read_core_rules_skips_upstream_exclusions(tmp_vault):
    _tier(tmp_vault).mkdir(exist_ok=True)
    (_tier(tmp_vault) / 'core_rule_priority_model.md').write_text(
        '---\nmetadata:\n  level: reminder\n---\n**Describe the system.**\n')
    (_tier(tmp_vault) / 'README.md').write_text('not a rule')
    (_tier(tmp_vault) / 'enforcement.md').write_text('not a rule')
    # no imperative at all (no frontmatter, no bold) -> excluded
    _write_rule(tmp_vault, 'core_plain', imperative=None, body='plain body without emphasis\n')
    _write_rule(tmp_vault, 'core_hidden', imperative='Only frontmatter-level rules.', extra_fm='inject: false')
    v = Vault(tmp_vault)
    assert v.read_core_rules() == ''  # every candidate excluded -> empty render

def test_read_core_rules_gated_by_settings(tmp_vault):
    _write_rule(tmp_vault, 'core_cheap', imperative='Cheap rule allowed.')
    _write_rule(tmp_vault, 'core_expensive', imperative='Expensive rule withheld.',
                extra_fm='gated_by: allow_expensive')
    v = Vault(tmp_vault)
    assert 'Expensive rule withheld.' in v.read_core_rules()  # unset gate -> included
    _write_settings(tmp_vault, {'allow_expensive': 'off'})
    out = v.read_core_rules()
    assert 'Expensive rule withheld.' not in out
    assert 'Cheap rule allowed.' in out

def test_read_core_rules_budget_from(tmp_vault):
    _write_rule(tmp_vault, 'core_budgeted', imperative='Stay inside the task budget.',
                extra_fm='budget_from: max_tasks')
    _write_settings(tmp_vault, {'max_tasks': 7})
    out = Vault(tmp_vault).read_core_rules()
    assert '1. Stay inside the task budget.' in out
    assert '   (max_tasks: at most 7)' in out
    _write_settings(tmp_vault, {'max_tasks': 'auto'})
    assert '   (max_tasks: as many as the machine allows)' in Vault(tmp_vault).read_core_rules()

def test_read_core_rules_lite_set(tmp_vault):
    _write_rule(tmp_vault, 'core_test_before_commit', imperative='Tiny rule.')
    _write_rule(tmp_vault, 'core_full_only', imperative='Full-only rule.')
    v = Vault(tmp_vault)
    assert 'Full-only rule.' in v.read_core_rules()  # unset rules_set -> full set
    _write_settings(tmp_vault, {'rules_set': 'lite'})
    out = v.read_core_rules()
    assert 'Tiny rule.' in out
    assert 'Full-only rule.' not in out

def test_read_core_rules_truncation_note(tmp_vault):
    for n in ('core_aaa', 'core_bbb', 'core_ccc'):
        _write_rule(tmp_vault, n, imperative=f'Rule {n}.')
    out = Vault(tmp_vault).read_core_rules(max_lines=3)  # header + 2 rules fit
    assert '1. Rule core_aaa.' in out
    assert '2. Rule core_bbb.' in out
    assert 'Rule core_ccc.' not in out
    assert '(1 core rules withheld: core_rules_max_lines cap reached)' in out

def test_parse_rule_bold_fallback(tmp_vault):
    d = tmp_vault / 'core-rules'
    d.mkdir(exist_ok=True)
    (d / 'core_bold.md').write_text(
        '---\nmetadata:\n  level: core\n  enforcement: reminder\n---\n'
        'Prose first.\n\n**Rule stays in the bold statement.**\n')
    (d / 'core_plain.md').write_text(
        '---\nmetadata:\n  level: core\n  enforcement: reminder\n---\nbody without emphasis\n')
    v = Vault(tmp_vault)
    assert v._parse_rule(d / 'core_bold.md')['imperative'] == 'Rule stays in the bold statement.'
    assert 'imperative' not in v._parse_rule(d / 'core_plain.md')

def test_read_core_rules_requires_tier_contract(tmp_vault):
    v = Vault(tmp_vault)
    assert v.read_core_rules() == ''  # no core-rules dir at all
    (_tier(tmp_vault)).mkdir()
    (_tier(tmp_vault) / 'core_lonely.md').write_text('---\nmetadata:\n  level: core\n  enforcement: reminder\nimperative: "Lonely."\n---\n')
    v = Vault(tmp_vault)
    assert v.read_core_rules() == ''  # dir but no core_rule_priority_model.md marker -> not the tier
    (_tier(tmp_vault) / 'core_rule_priority_model.md').write_text('marker\n')
    out = v.read_core_rules()
    assert '1. Lonely.' in out

def test_core_rules_on_real_vault_matches_upstream_pipeline():
    """End-to-end: the adapter render of Emmanuel's vault must equal text built
    by the upstream 0.22.1 pipeline (gt_paths.find_core_rules + core_rule_files
    + parse_rule, full-mode defaults). Skipped when the upstream scripts or the
    vault are not present on the running host."""
    from pathlib import Path
    v_root = Path('/mnt/obsidian-vaults/eman-mobile/golden-vault')
    gt_up = Path(__file__).resolve().parents[3] / 'golden-thread-plugin' / 'golden-thread' / '0.22.1'
    if not (v_root / 'core-rules').is_dir() or not gt_up.exists():
        import pytest
        pytest.skip('upstream scripts or vault not present')
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'gt_paths_parity', str(gt_up / 'scripts' / 'gt_paths.py'))
    assert spec is not None and spec.loader is not None
    gt_paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gt_paths)
    tier = gt_paths.find_core_rules(v_root)
    up = []
    for p in gt_paths.core_rule_files(tier):
        if p.name == gt_paths.MODEL_FILE:
            continue
        r = gt_paths.parse_rule(p)
        if r.get('level') != 'core' or not r.get('imperative'):
            continue
        if str(r.get('inject', '')).lower() == 'false':
            continue
        up.append(r)
    up.sort(key=lambda r: (r.get('enforcement') != 'validated', r['name']))
    mine = Vault(v_root).read_core_rules().splitlines()
    exp_header = f'CORE RULES (always on, every turn, every project — see {tier.relative_to(v_root)}):'
    assert mine[0] == exp_header
    assert len(mine) - 1 == len(up)
    for i, r in enumerate(up):
        assert mine[i + 1] == f"{i+1}. {r['imperative']}"

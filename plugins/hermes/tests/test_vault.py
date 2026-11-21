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

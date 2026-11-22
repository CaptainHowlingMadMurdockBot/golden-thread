from golden_thread_hermes.memory.project import ProjectManager
from golden_thread_hermes.memory.vault import Vault

def test_log_and_decision(tmp_vault):
    v = Vault(str(tmp_vault))
    pm = ProjectManager(v, 'demo')
    pm.log('test entry')
    log_file = next((tmp_vault / 'Projects' / 'demo' / 'Log').glob('*.md'))
    assert 'test entry' in log_file.read_text()
    adr = pm.decide('ADR-001', 'we need one', 'pick X', 'simpler')
    assert adr == 'ADR-0001'
    assert 'pick X' in pm.read_context()

def test_open_sets_active(tmp_vault):
    v = Vault(str(tmp_vault))
    pm = ProjectManager(v)
    pm.open('demo')
    assert pm.active_slug == 'demo'

def test_create_and_activate(tmp_vault):
    v = Vault(str(tmp_vault))
    pm = ProjectManager(v)
    slug = pm.create('Other', 'second project')
    assert slug == 'other'
    assert pm.active_slug == 'other'
    assert (tmp_vault / 'Projects' / 'other' / 'idea.md').exists()

def test_create_nested_slug_not_flattened(tmp_vault):
    # Regression: gt_create('personal/finance') used to slugify away the '/'
    # and create Projects/personalfinance (see rpg/scarlet-heroes, 2026-10-27).
    v = Vault(str(tmp_vault))
    pm = ProjectManager(v)
    slug = pm.create('personal/finance', 'nested project')
    assert slug == 'personal/finance'
    assert (tmp_vault / 'Projects' / 'personal' / 'finance' / 'idea.md').exists()
    assert not (tmp_vault / 'Projects' / 'personalfinance').exists()

def test_create_seeds_readme(tmp_vault):
    # Regression: gt_create used to scaffold without README.md, which gt_task,
    # minimize task rollups and resolve_project all require (## Tasks contract).
    v = Vault(str(tmp_vault))
    pm = ProjectManager(v)
    slug = pm.create('nested/readme-test', 'readme scaffold check')
    readme = tmp_vault / 'Projects' / 'nested' / 'readme-test' / 'README.md'
    assert readme.exists()
    text = readme.read_text()
    assert '## Tasks' in text
    assert f'slug: {slug}' in text

def test_slugify_path(tmp_vault):
    v = Vault(str(tmp_vault))
    assert v.slugify_path('personal/finance') == 'personal/finance'
    assert v.slugify_path('My Fancy/ Project') == 'my-fancy/project'
    assert v.slugify_path('single') == 'single'
    assert v.slugify_path('trailing/') == 'trailing'
    assert v.slugify_path('') == 'project'

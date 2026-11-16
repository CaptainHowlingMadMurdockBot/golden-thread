from golden_thread_hermes.memory.vault import Vault

def test_vault_core_read(tmp_vault):
    v = Vault(str(tmp_vault))
    assert 'Body trust' in v.read_core()
    assert v.list_projects() == ['demo']

def test_slugify(tmp_vault):
    v = Vault(str(tmp_vault))
    assert v.slugify('Hello World!') == 'hello-world'

def test_list_projects_includes_subprojects(tmp_vault):
    v = Vault(str(tmp_vault))
    child = tmp_vault / 'Projects' / 'demo' / 'subdemo'
    child.mkdir()
    (child / 'idea.md').write_text('# Sub\n')
    (tmp_vault / 'Projects' / 'demo' / 'junkdir').mkdir()  # no README/idea.md -> not a project
    assert v.list_projects() == ['demo', 'demo/subdemo']

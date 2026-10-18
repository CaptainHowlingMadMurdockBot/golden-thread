import json
from unittest.mock import patch

import json
from golden_thread_hermes.memory.provider import GoldenThreadMemoryProvider
from golden_thread_hermes.memory.config import GoldenThreadConfig

def make_provider(vault):
    p = GoldenThreadMemoryProvider()
    cfg = GoldenThreadConfig(vault)
    with patch.object(GoldenThreadConfig, 'load', return_value=cfg):
        with patch.object(GoldenThreadConfig, 'save'):
            p.initialize('test-session')
    return p

def test_provider_lifecycle(tmp_vault):
    p = make_provider(tmp_vault)
    assert p.is_available()

def test_provider_schemas(tmp_vault):
    p = make_provider(tmp_vault)
    schemas = p.get_tool_schemas()
    names = sorted([s['name'] for s in schemas])
    assert names == ['gt_create', 'gt_decide', 'gt_list', 'gt_open', 'gt_work']

def test_provider_system_prompt(tmp_vault):
    p = make_provider(tmp_vault)
    # open a project first so the provider reports it as active
    json.loads(p.handle_tool_call('gt_open', {'project_slug': 'demo'}))
    block = p.system_prompt_block()
    assert 'Body trust' in block
    assert 'demo' in block

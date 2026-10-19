import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, '..')))
_spec = importlib.util.spec_from_file_location(
    'rotation', os.path.join(ROOT, 'scripts', 'update_openrouter_recommendation.py'))
rot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rot)


def test_check_language():
    assert rot.check_language('Выходные норм, ел борщ. Погода как всегда.') is None
    assert rot.check_language('Пиво это 液体系统 штука') == 'CJK characters in reply'
    assert rot.check_language('Ну when least expects, короче') == 'Latin words in reply'
    assert rot.check_language('Купил в Rimi хлеб') is None  # one brand name is fine
    assert rot.check_language('') == 'empty reply'
    assert rot.check_language('Just English') == 'no Cyrillic in reply'


def test_parse_candidates_filters_tools_and_blocklist():
    payload = {'models': [
        {'id': 'a:free', 'supportsTools': True},
        {'id': 'b:free', 'supportsTools': False},
        {'id': 'c:free', 'supportsTools': True},
    ], 'fallback': {'id': 'openrouter/free'}}
    cands, fb = rot.parse_candidates(payload, blocklist={'a:free'})
    assert cands == ['c:free']
    assert fb == 'openrouter/free'


def test_pick_model_takes_first_passing():
    results = {'a': 'CJK characters in reply', 'b': None, 'c': None}
    model, rejected = rot.pick_model('k', ['a', 'b', 'c'], check=lambda _k, m: results[m])
    assert model == 'b'
    assert rejected == {'a': 'CJK characters in reply'}


def test_pick_model_none_pass():
    model, rejected = rot.pick_model('k', ['a'], check=lambda _k, m: 'no tool call')
    assert model is None and rejected == {'a': 'no tool call'}


def test_only_opted_in_env_files_are_rotated(tmp_path, monkeypatch):
    (tmp_path / '.env.or_bot').write_text('OPENROUTER_API_KEY=x\nOPENROUTER_MODEL=old\n')
    (tmp_path / '.env.openai_bot').write_text('OPENROUTER_API_KEY=x\nOPENAI_MODEL=gpt\n')
    (tmp_path / '.env.or_bot.bak.1').write_text('OPENROUTER_API_KEY=x\nOPENROUTER_MODEL=old\n')
    files = rot.openrouter_env_files(tmp_path)
    assert [p.name for p in files] == ['.env.or_bot']
    assert rot.update_env_file(files[0], 'new', 'openrouter/free')
    text = files[0].read_text()
    assert 'OPENROUTER_MODEL=new' in text and 'OPENROUTER_FALLBACK_MODEL=openrouter/free' in text


def test_rotation_throttle(tmp_path, monkeypatch):
    monkeypatch.setattr(rot, 'STAMP_FILE', tmp_path / 'stamp.json')
    assert rot.rotation_due(1000.0)
    rot.write_stamp(1000.0, 'm', {})
    assert not rot.rotation_due(1000.0 + 3600)
    assert rot.rotation_due(1000.0 + rot.ROTATE_HOURS * 3600)
    assert json.loads((tmp_path / 'stamp.json').read_text())['model'] == 'm'


def test_red_square_png_is_valid():
    import base64 as b64
    data = b64.b64decode(rot.VISION_TEST_IMAGE.split(",", 1)[1])
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82")


def test_order_vision_candidates():
    ranked = ['a:free', 'b:free', 'c:free']
    free_vision = ['x:free', 'b:free', 'a:free']
    assert rot.order_vision_candidates(ranked, free_vision) == ['a:free', 'b:free', 'x:free']


def test_vision_preflight(monkeypatch):
    monkeypatch.setattr(rot, '_chat', lambda *a, **k: {'content': 'Красный'})
    assert rot.vision_preflight('k', 'm') is None
    monkeypatch.setattr(rot, '_chat', lambda *a, **k: {'content': 'Синий'})
    assert 'did not see' in rot.vision_preflight('k', 'm')


def test_vision_env_files_and_update(tmp_path):
    (tmp_path / '.env').write_text('OPENROUTER_API_KEY=x\nOPENROUTER_VISION_MODEL=dead:free\n')
    (tmp_path / '.env.bot').write_text('OPENROUTER_VISION_MODEL=old\n')
    (tmp_path / '.env.other').write_text('OPENAI_MODEL=gpt\n')
    files = rot.vision_env_files(tmp_path)
    assert [p.name for p in files] == ['.env', '.env.bot']
    assert rot.update_env_key(files[0], rot.VISION_ENV_KEY, 'new:free')
    assert 'OPENROUTER_VISION_MODEL=new:free' in files[0].read_text()
    assert not rot.update_env_key(files[0], rot.VISION_ENV_KEY, 'new:free')

"""文件盘点只读、敏感内容隔离、边界整套和候选哈希契约。"""
import json
from pathlib import Path

import pytest

from scripts.inventory_files import build_inventory, main, stable_digest


def test_inventory_skips_dependencies_and_never_opens_credentials(tmp_path, monkeypatch):
    for folder in ['venv312', '.venv', '.test-run-old']:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'hidden.py').write_text('not scanned', encoding='utf-8')
    for name in ['.env', 'Deepseek.txt', 'personal_notes.txt', 'api_key.json']:
        (tmp_path / name).write_text('private fixture only', encoding='utf-8')
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.name in {'.env', 'Deepseek.txt', 'personal_notes.txt', 'api_key.json'}:
            pytest.fail('不得读取私密内容')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    result = build_inventory(tmp_path, 'private', True)
    assert len(result['files']) == 4 and len(result['skipped_directories_or_links']) == 3
    assert result['summary']['hashed_files'] == 0 and result['archive_actions'] == []
    assert all(f['action'] == '保留原位' for f in result['files'])


def test_inventory_hashes_only_duplicate_candidates_and_preserves_gis(tmp_path):
    for name in ['a.json', 'b.json']:
        (tmp_path / name).write_text('[1]', encoding='utf-8')
    (tmp_path / 'unique.json').write_text('[1, 2]', encoding='utf-8')
    for suffix in ['shp', 'shx', 'dbf', 'prj']:
        (tmp_path / ('boundary.' + suffix)).write_bytes(suffix.encode())
    (tmp_path / 'incomplete.shp').write_bytes(b'other')
    result = build_inventory(tmp_path, hash_duplicates=True)
    assert result['summary']['hashed_files'] == 2
    assert result['duplicate_groups'][0]['paths'] == ['a.json', 'b.json']
    bundles = {b['group']: b for b in result['boundary_bundles']}
    assert not bundles['boundary']['missing_required']
    assert bundles['incomplete']['missing_required'] == ['.dbf', '.prj', '.shx']
    assert result['summary']['moved'] == result['summary']['deleted'] == 0
    assert len(list(tmp_path.iterdir())) == 8


def test_inventory_reference_evidence_is_not_dead_code_proof(tmp_path):
    (tmp_path / 'app.py').write_text('from worker import main\n', encoding='utf-8')
    (tmp_path / 'worker.py').write_text('def main(): pass\n', encoding='utf-8')
    (tmp_path / 'unused.py').write_text('x = 1\n', encoding='utf-8')
    result = build_inventory(tmp_path, 'repository')
    records = {r['path']: r for r in result['files']}
    assert records['worker.py']['references'] == [{'file': 'app.py', 'line': 1, 'kind': '名称/路径引用线索'}]
    assert not records['unused.py']['references']
    assert records['unused.py']['action'] == '保留原位'


def test_inventory_output_stays_inside_authorized_root_and_is_new(tmp_path, capsys):
    root = tmp_path / 'private'
    root.mkdir()
    source = root / 'notes.txt'
    source.write_text('private fixture', encoding='utf-8')
    for output in [tmp_path / 'inventory-outside.json', source, root / 'news.json']:
        with pytest.raises(SystemExit):
            main(['--root', str(root), '--output', str(output)])
    target = root / 'inventory-test.json'
    assert main(['--root', str(root), '--output', str(target)]) == 0
    assert json.loads(target.read_text(encoding='utf-8'))['summary']['file_count'] == 1
    with pytest.raises(SystemExit):
        main(['--root', str(root), '--output', str(target)])
    assert 'private fixture' not in capsys.readouterr().out
    assert source.read_text(encoding='utf-8') == 'private fixture'


def test_inventory_rejects_changed_hash_input(tmp_path):
    path = tmp_path / 'data.json'
    path.write_bytes(b'[1]')
    expected = (path.stat().st_size, path.stat().st_mtime_ns)
    path.write_bytes(b'[1,2]')
    with pytest.raises(RuntimeError):
        stable_digest(path, expected)

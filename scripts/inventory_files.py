"""只读文件治理清单：不读取凭据/私人笔记，不移动、删除或导入资料。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

SKIP_DIRS = {'.git', '.venv', 'venv', 'venv312', 'py312', 'env', 'node_modules',
             '__pycache__', '.pytest_cache', '.idea', '.vscode', '.qoder', 'site-packages'}
TEXT_CODE = {'.py', '.js', '.html', '.css', '.md', '.yaml', '.yml', '.toml', '.ini', '.sh'}
GIS_PARTS = {'.shp', '.shx', '.dbf', '.prj', '.cpg', '.sbn', '.sbx', '.qix'}
HASH_FORMATS = {'.json', '.jsonl', '.csv', '.geojson', '.png', '.jpg', '.jpeg', '.webp',
                '.pdf', '.docx', '.zip', '.tif', '.tiff'} | GIS_PARTS
SECRET = re.compile(r'(?i)(^\.env(?:\.|$)|secret|credential|password|token|api.?key|deepseek|密钥|密码|凭据)')


def excluded_dir(path):
    return (path.name.lower() in SKIP_DIRS or path.name.startswith('.test-run')
            or path.is_symlink() or os.path.isjunction(path) or (path / 'pyvenv.cfg').is_file())


def sensitive(path):
    return bool(SECRET.search(path.name)) or path.suffix.lower() in {'.pem', '.key', '.pfx', '.p12'}


def enumerate_files(root):
    """不跟随目录链接；虚拟环境仅记录目录元数据，不遍历依赖代码。"""
    skipped, errors, files = [], [], []
    def onerror(exc):
        errors.append({'operation': 'scan', 'error': type(exc).__name__})
    for current, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        kept = []
        for name in sorted(dirs):
            path = Path(current, name)
            if excluded_dir(path):
                skipped.append(path.relative_to(root).as_posix())
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = Path(current, name)
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                skipped.append(path.relative_to(root).as_posix())
                continue
            files.append(path)
    return files, skipped, errors


def classify(relative):
    name, parts = relative.name.lower(), {p.lower() for p in relative.parts}
    if sensitive(relative):
        return '受保护配置', '禁止读取、哈希或归档；仅记录元数据', False
    if relative.suffix.lower() in GIS_PARTS or any(p.endswith('.gdb') for p in parts) or 'gadm' in '/'.join(parts):
        return '科研原始资料', '边界及附属文件必须整套保留；版本和许可需核查', False
    if any('标注' in p or 'annotation' in p for p in parts) or 'historical_events' in name:
        return '人工标注', '人工成果，不因已入库或重复而自动归档', False
    if name.endswith('_seen.txt') or 'checkpoint' in name or 'watermark' in name:
        return '活动运行状态', '去重记录/水位不得搬离活动路径', False
    if parts & {'raw', 'processed', 'external', '运行数据', 'logs'} or name.endswith('.log'):
        return '活动运行资料', '原始新闻、历史评分、运行产物和日志保留原位', False
    if name.startswith(('myanmar_news_', 'gdelt_news_', 'rss_news_', 'myanmar_now_')):
        return '科研原始资料', '新闻原始快照，许可与来源应从采集记录核验', False
    if name.endswith(('.png', '.jpg', '.jpeg', '.webp')) and len(relative.parts) == 1 and any(t in name for t in ('dashboard', 'screenshot', 'snapshot')):
        return '派生产物', '疑似界面截图；仍须核查文档引用与可重建性', True
    if parts & {'static', 'templates', 'analyzer', 'data', 'storage', 'migrations', 'deploy', 'utils', 'visualization', 'scripts', 'tests'} or relative.suffix.lower() in TEXT_CODE:
        return '项目代码或文档', '运行/测试/CLI/文档入口均可能动态引用；无匹配不等于未使用', False
    return '待人工分类', '不读取任意私人笔记正文，来源、许可和可重建性待确认', False


def reference_index(root, files, scope):
    """只索引仓库代码/文档中的名称引用，不输出匹配行内容。"""
    if scope != 'repository':
        return []
    texts = []
    for path in files:
        rel = path.relative_to(root)
        if sensitive(path) or path.suffix.lower() not in TEXT_CODE:
            continue
        if set(rel.parts) & {'vendor', 'raw', 'processed', 'external', 'private'}:
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            text = path.read_text(encoding='utf-8-sig')
            texts.append((rel.as_posix(), text.splitlines()))
        except (OSError, UnicodeError):
            continue
    return texts


def references(relative, texts):
    tokens = {relative.name, relative.as_posix()}
    if relative.suffix == '.py':
        tokens.add('.'.join(relative.with_suffix('').parts))
        if relative.stem != '__init__':
            tokens.add(relative.stem)
    pattern = re.compile(r'(?<![\w])(?:' + '|'.join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r')(?![\w])')
    found = []
    for name, lines in texts:
        if name == relative.as_posix():
            continue
        for line, text in enumerate(lines, 1):
            if pattern.search(text):
                found.append({'file': name, 'line': line, 'kind': '名称/路径引用线索'})
    return found


def stable_digest(path, expected):
    before = path.stat()
    if (before.st_size, before.st_mtime_ns) != expected:
        raise RuntimeError('文件自清单扫描后已变化')
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) != expected:
        raise RuntimeError('哈希期间文件发生变化')
    return value.hexdigest()


def build_inventory(root, scope='private', hash_duplicates=False):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or scope not in {'private', 'repository'}:
        raise ValueError('需要明确授权的目录与有效清单范围')
    files, skipped, errors = enumerate_files(root)
    texts = reference_index(root, files, scope)
    entries, candidates, shapefiles, geodatabases = [], defaultdict(list), defaultdict(list), defaultdict(list)
    for path in files:
        rel = path.relative_to(root)
        try:
            stat = path.stat()
        except OSError as exc:
            errors.append({'file': rel.as_posix(), 'operation': 'stat', 'error': type(exc).__name__})
            continue
        category, reason, rebuild_candidate = classify(rel)
        refs = references(rel, texts) if not sensitive(path) else []
        entry = {'path': rel.as_posix(), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                 'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                 'category': category, 'reason': reason, 'references': refs,
                 'reference_status': '未扫描私密正文' if scope == 'private' else '仅静态名称匹配；动态加载未完全证明',
                 'license': '未核验，不能由文件来源或扩展名推定再分发权',
                 'rebuild_status': '待验证' if rebuild_candidate else '未证明可重建',
                 'action': '保留原位', 'sha256': None}
        entries.append(entry)
        if not sensitive(path) and path.suffix.lower() in HASH_FORMATS and stat.st_size:
            candidates[(stat.st_size, path.suffix.lower())].append((path, entry))
        if path.suffix.lower() in GIS_PARTS:
            shapefiles[rel.with_suffix('').as_posix()].append(rel.as_posix())
        for parent in rel.parents:
            if parent.suffix.lower() == '.gdb':
                geodatabases[parent.as_posix()].append(rel.as_posix())
                break
    duplicates = []
    if hash_duplicates:
        for group in candidates.values():
            if len(group) < 2:
                continue
            by_hash = defaultdict(list)
            for path, entry in group:
                try:
                    entry['sha256'] = stable_digest(path, (entry['bytes'], entry['mtime_ns']))
                    by_hash[entry['sha256']].append(entry['path'])
                except (OSError, RuntimeError) as exc:
                    errors.append({'file': entry['path'], 'operation': 'hash', 'error': type(exc).__name__})
            for checksum, paths in by_hash.items():
                if len(paths) > 1:
                    duplicates.append({'sha256': checksum, 'paths': paths,
                                       'decision': '字节相同不证明用途相同；未批准移动'})
    bundles = []
    for stem, paths in sorted(shapefiles.items()):
        present = {Path(p).suffix.lower() for p in paths}
        bundles.append({'group': stem, 'type': 'Shapefile', 'files': paths,
                        'missing_required': sorted({'.shp', '.shx', '.dbf', '.prj'} - present),
                        'crs': '须核验同组prj，未假定EPSG:4326', 'action': '整套保留'})
    for group, paths in sorted(geodatabases.items()):
        bundles.append({'group': group, 'type': 'FileGDB', 'files': paths, 'action': '整目录保留'})
    return {'version': 'inventory-v1', 'mode': 'read-only', 'scope': scope,
            'created_at': datetime.now(timezone.utc).isoformat(), 'files': entries,
            'skipped_directories_or_links': skipped, 'errors': errors,
            'duplicate_groups': duplicates, 'boundary_bundles': bundles,
            'archive_actions': [], 'restore': '未移动文件，无需恢复；后续归档须另记原路径、目标路径、哈希和审批依据',
            'summary': {'file_count': len(entries), 'bytes': sum(e['bytes'] for e in entries),
                        'categories': dict(Counter(e['category'] for e in entries)),
                        'duplicate_groups': len(duplicates), 'boundary_bundles': len(bundles),
                        'hashed_files': sum(e['sha256'] is not None for e in entries),
                        'errors': len(errors), 'moved': 0, 'deleted': 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读清单；私密清单只允许留在授权目录，不归档或删除')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--scope', choices=['private', 'repository'], default='private')
    parser.add_argument('--hash-duplicates', action='store_true', help='只哈希同大小同格式候选，不读取密钥或任意文本笔记')
    parser.add_argument('--output', type=Path, help='授权根目录内的全新 inventory-*.json 清单')
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    output = args.output.resolve() if args.output else None
    if output and (not output.is_relative_to(root) or output.exists() or not output.parent.is_dir()
                   or not re.fullmatch(r'inventory-[\w-]+\.json', output.name)):
        parser.error('清单必须为授权目录内的全新 inventory-*.json，禁止覆盖或写到目录外')
    report = build_inventory(root, args.scope, args.hash_duplicates)
    if output:
        if not output.parent.resolve().is_relative_to(root):
            parser.error('清单输出路径发生变化')
        with output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 2 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

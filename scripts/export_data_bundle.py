"""生成离线数据同步包：把本机分析结果与待审夜光资料打给部署方，不经公开仓库。

设计约束：
- 输出目录必须在本项目之外，且不能落在私密资料根目录内，避免误提交或覆盖交付原件。
- 只按白名单收集：派生指标、事件库、数据源健康、新闻原文与去重清单、夜光待审库。
- 绝不收集 .env、密钥、Cookie、用户账号或整个私密目录。
- 生成 MANIFEST.json（逐文件 SHA256 + 记录数 + 数据新鲜度）与独立可运行的 verify_bundle.py。

用法：
    python scripts/export_data_bundle.py --out <项目外的空目录> [--label 说明]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUNDLE_SPEC = 'mmr-risk-data-bundle/v1'
NEWS_PREFIXES = ('myanmar_news_', 'gdelt_news_', 'myanmar_now_', 'rss_news_')
DEDUPE_FILES = ('urls_seen.txt', 'rss_urls_seen.txt', 'english_urls_seen.txt', 'gdelt_urls_seen.txt')
PROCESSED_FILES = ('daily_risk.jsonl', 'risk_scores.jsonl', 'gdelt_event_store.json',
                   'gdelt_csv_state.json', 'source_health.json')
# 明显不应外带的名字：环境文件、密钥、Cookie、账号库
DENY = re.compile(r'(\.env$|cookie|secret|credential|passwor|\.key$|\.pem$)', re.I)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def guarded_copy(source: Path, destination: Path, allowed_root: Path) -> None:
    """只允许从真实数据根内取文件，并拦截疑似密钥/账号/Cookie 名称。"""
    if DENY.search(source.name):
        raise ValueError(f'拒绝打包疑似敏感文件：{source.name}')
    resolved = source.resolve()
    if not resolved.is_relative_to(allowed_root) or resolved.is_symlink():
        raise ValueError(f'源文件越出或位于链接之外：{source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination)


def jsonl_records(path: Path) -> int:
    with path.open(encoding='utf-8', errors='replace') as handle:
        return sum(1 for line in handle if line.strip())


def review_database(path: Path) -> dict:
    """只读统计待审库；确认没有任何记录获得正式评分资格。"""
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        counts = dict(connection.execute(
            "SELECT kind, count(*) FROM pending_records GROUP BY kind"))
        eligible = connection.execute(
            'SELECT count(*) FROM pending_records WHERE eligible_for_scoring<>0').fetchone()[0]
        assets = connection.execute('SELECT count(*) FROM assets').fetchone()[0]
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        connection.close()
    if eligible or integrity != 'ok':
        raise ValueError(f'待审库异常：可评分记录 {eligible}，完整性 {integrity}')
    return {'assets': assets, 'record_counts': counts, 'scoring_eligible': eligible}


def collect(paths: dict, bundle: Path) -> list:
    entries = []
    raw_source, processed_source = Path(paths['raw']), Path(paths['processed'])
    review_source = Path(paths['external']) / 'team_delivery_review'

    plan = []
    for name in PROCESSED_FILES:
        source = processed_source / name
        if source.is_file():
            plan.append((source, bundle / 'runtime_snapshot' / 'processed' / name))
    for source in sorted(raw_source.glob('*.json')):
        if source.name.startswith(NEWS_PREFIXES):
            plan.append((source, bundle / 'runtime_snapshot' / 'raw' / source.name))
    for name in DEDUPE_FILES:
        source = raw_source / name
        if source.is_file():
            plan.append((source, bundle / 'runtime_snapshot' / 'raw' / name))
    if review_source.is_dir():
        for source in sorted(review_source.rglob('*')):
            if source.is_file():
                plan.append((source, bundle / 'nightlight_review' / source.relative_to(review_source)))
    else:
        print('提示：未找到夜光待审目录，包内将不含该部分', flush=True)

    allowed = Path(paths['root']).resolve()
    for source, target in plan:
        guarded_copy(source, target, allowed)
        entries.append(target)
    return entries


def freshness(paths: dict) -> dict:
    """让代码自己报告真实落点与新鲜度，避免手工拼路径误判。"""
    from data.event_store import get_event_store
    from utils.data_contract import FORMAL_MODES

    daily = Path(paths['processed']) / 'daily_risk.jsonl'
    records = []
    if daily.is_file():
        with daily.open(encoding='utf-8') as handle:
            records = [json.loads(line) for line in handle if line.strip()]
    formal = [r for r in records if r.get('run_kind') in FORMAL_MODES]
    dates = sorted({r['date'] for r in formal})
    store = get_event_store()
    from data.source_health import get_source_health_tracker
    # 落盘文件只存原始记录，状态必须由追踪器自己算，避免重复实现口径
    health = get_source_health_tracker().get_health()
    return {'data_root': str(Path(paths['root']).resolve()), 'business_days': len(dates),
            'earliest': dates[0] if dates else None, 'latest': dates[-1] if dates else None,
            'formal_records': len(formal),
            'event_store': store.stats(), 'event_watermark': store.checkpoint(),
            'source_status': {name: item.get('status') for name, item in health.items()}}


def code_revision() -> str:
    import subprocess
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                              text=True, timeout=20).stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


VERIFY = '''"""独立校验数据同步包：只依赖标准库，收到后先跑这个。"""
import hashlib, json, sys
from pathlib import Path

bundle = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent)
manifest = json.loads((bundle / 'MANIFEST.json').read_text(encoding='utf-8'))
bad, missing = [], []
for item in manifest['files']:
    path = bundle / item['path']
    if not path.is_file():
        missing.append(item['path']); continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item['sha256'] or path.stat().st_size != item['bytes']:
        bad.append(item['path'])
print('应到文件', len(manifest['files']), '| 缺失', len(missing), '| 校验不符', len(bad))
for name in (missing + bad)[:10]:
    print('  问题：', name)
print('数据新鲜度：', manifest['freshness'].get('earliest'), '~', manifest['freshness'].get('latest'),
      '| 事件水位', manifest['freshness'].get('event_watermark'))
print('待审夜光可评分记录：', manifest['nightlight']['scoring_eligible'], '（必须为 0）')
print('结论：', '包完整可用' if not missing and not bad and manifest['nightlight']['scoring_eligible'] == 0 else '请勿使用，先重新导出')
sys.exit(0 if not missing and not bad and manifest['nightlight']['scoring_eligible'] == 0 else 1)
'''

README = """# 缅甸地缘风险系统 · 离线数据同步包

生成时间：{generated}　｜　导出代码版本：{revision}　｜　规格：{spec}

## 一、这个包是什么

包内是**本机已算好的分析结果与待审资料**，用于让部署在服务器上的同一套系统直接显示真实数据，
而不是空面板。它**不含** `.env`、API 密钥、账号数据或 Cookie。

| 目录 | 内容 | 服务器用途 |
|---|---|---|
| `runtime_snapshot/processed/` | 日风险指标、风险分数、GDELT 事件库与增量水位、数据源健康 | 态势总览、趋势、预警、地图、空间统计 |
| `runtime_snapshot/raw/` | 新闻原文（四类白名单前缀）与 URL 去重清单 | 事件与证据页、重算流水线 |
| `nightlight_review/` | 夜光 QC 待审库、影像核验报告、文档正文提取 | **仅供人工审定，不参与评分** |

## 二、放置方法

把 `runtime_snapshot/` 的内容放到服务器上的数据根目录（与本机 `DATA_ROOT` 同结构）：

```
DATA_ROOT/
  raw/          ← runtime_snapshot/raw/
  processed/    ← runtime_snapshot/processed/
  external/     ← nightlight_review/ 放到 external/team_delivery_review/
```

启动前设置环境变量 `DATA_ROOT` 指向该目录（或写进 `.env`），存储后端保持 `STORAGE_BACKEND=file`。
文件缺失时系统会显示缺测，不会用零值或旧值填充。

## 三、必须先知道的三件事

1. **境外源在服务器上大概率爬不动。** Google News、Irrawaddy、Kachin、GDELT 需要代理；
   若服务器无出口代理，采集会持续失败，此时**只能靠本包的数据**，请定期从本机再导一次。
2. **`nightlight_review/` 是待审层，不是正式观测。** 库内 `eligible_for_scoring` 恒为 0，
   夜光接入评分需等口径决策（分母、覆盖门槛、负值、跨卫星）审定后另行实现。
3. **本包含第三方新闻原文与内部文档摘录，属受限资料。** 只走私有渠道传递，
   不要提交到公开仓库、不要挂到公网可下载的地址、不要放进网盘公开分享。

## 四、收到后请先自检

```
python verify_bundle.py
```

逐文件比对 SHA256 与字节数，并确认待审库可评分记录为 0。校验不通过请重新导出，不要带病上线。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='项目外的空目录')
    parser.add_argument('--label', default='', help='备注，写入 MANIFEST')
    args = parser.parse_args()

    out = args.out.resolve()
    if ROOT in out.parents or out == ROOT:
        sys.exit('输出目录必须位于项目之外，避免数据被误提交')
    from utils.config import get_data_paths
    paths = get_data_paths()
    if Path(paths['root']).resolve() in out.parents:
        sys.exit('输出目录不能落在私密数据根之内，请另选位置')
    if out.exists() and any(out.iterdir()):
        sys.exit('输出目录非空，请指定空目录以免混入旧文件')

    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    bundle = out / f'mmr_data_bundle_{stamp}'
    if bundle.exists():
        sys.exit('同一分钟内已有导出目录，请稍后重试以免新旧文件混在一个包里')
    bundle.mkdir(parents=True)
    print('真实数据根：', paths['root'], flush=True)

    files = collect(paths, bundle)
    review_db = bundle / 'nightlight_review' / 'review.sqlite3'
    nightlight = review_database(review_db) if review_db.is_file() else {'absent': True}
    fresh = freshness(paths)

    entries = []
    for path in sorted(bundle.rglob('*')):
        if path.is_file() and path.name != 'MANIFEST.json':
            entries.append({'path': path.relative_to(bundle).as_posix(),
                            'bytes': path.stat().st_size, 'sha256': sha256(path)})
    generated = datetime.now(timezone.utc).isoformat(timespec='seconds')
    manifest = {'spec': BUNDLE_SPEC, 'generated_at': generated, 'label': args.label,
                'code_revision': code_revision(), 'file_count': len(entries),
                'total_bytes': sum(e['bytes'] for e in entries), 'freshness': fresh,
                'nightlight': nightlight,
                'excluded': ['.env', 'API密钥与Cookie', '用户账号与会话', 'analysis_*.json 单次运行产物',
                             'demo/manual 非正式数据域'],
                'files': entries}
    (bundle / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (bundle / 'verify_bundle.py').write_text(VERIFY, encoding='utf-8')
    (bundle / 'README_数据说明.md').write_text(README.format(
        generated=generated, revision=manifest['code_revision'][:12], spec=BUNDLE_SPEC), encoding='utf-8')

    print(json.dumps({'bundle': str(bundle), 'files': len(entries),
                      'total_mb': round(manifest['total_bytes'] / 1e6, 1),
                      'latest_observation': fresh['latest'],
                      'business_days': fresh['business_days'],
                      'event_store': fresh['event_store'], 'event_watermark': fresh['event_watermark'],
                      'nightlight': nightlight}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

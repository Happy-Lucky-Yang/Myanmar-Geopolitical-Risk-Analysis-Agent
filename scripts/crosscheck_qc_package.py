"""交叉核对队友QC交付包与本地待审库：同月同列逐项比较，不改动任何原件。

只读：读取 review.sqlite3 的 nightlight_candidate 记录与交付包 CSV，输出差异清单。
用法：
  python scripts/crosscheck_qc_package.py --package <QC包目录> \
      --review-root <team_delivery_review目录> [--tolerance 5e-6]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys

# 交付包列 -> 本地统计键；两边都为独立实现，列名不同但口径应一致。
COLUMNS = {
    'base_pixels': 'inside_pixels',
    'valid_pixels': 'observed_pixels',
    'noobs_pixels': 'zero_coverage_pixels',
    'neg_share_of_valid': 'negative_fraction_observed',
    'rad_mean_valid': 'radiance_mean_observed',
    'cf_mean_valid': 'cf_mean_observed',
    'cf_mean_base': 'cf_mean_all_inside',
    'max_rad_valid': 'radiance_max',
}
INTEGER_COLUMNS = {'base_pixels', 'valid_pixels', 'noobs_pixels'}


def local_statistics(review_root: Path) -> dict:
    """读取待审库中全国口径的候选统计；待审记录不含评分资格。"""
    database = review_root / 'review.sqlite3'
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = [json.loads(payload) for (payload,) in connection.execute(
            "SELECT payload FROM pending_records WHERE kind='nightlight_candidate'")]
    finally:
        connection.close()
    result = {}
    for row in rows:
        if row['region'] != 'MMR':
            continue
        month = row['period_start'][:7].replace('-', '')
        if month in result:
            raise ValueError(f'待审库中{month}的全国记录重复，先排查来源')
        result[month] = {**row['statistics'], 'status': row['status']}
    return result


def package_rows(package: Path) -> dict:
    path = package / '01_四项核对' / '02_统计分母' / 'qc_table_valid.csv'
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    table = {}
    for row in rows:
        month = (row.get('ym') or '').strip()
        if not month.isdigit() or len(month) != 6:
            raise ValueError(f'交付表存在无法识别的月份：{month!r}')
        if month in table:
            raise ValueError(f'交付表中{month}重复')
        table[month] = row
    return table


def compare(package: Path, review_root: Path, tolerance: float) -> dict:
    delivered, local = package_rows(package), local_statistics(review_root)
    differences, checked = [], 0
    for month in sorted(set(delivered) | set(local)):
        if month not in delivered or month not in local:
            differences.append({'month': month, 'column': '-', 'issue': 'month_only_in_'
                                + ('package' if month in delivered else 'review')})
            continue
        for column, key in COLUMNS.items():
            raw = (delivered[month].get(column) or '').strip()
            mine = local[month].get(key)
            if raw == '' or mine is None:
                differences.append({'month': month, 'column': column, 'issue': 'missing_value',
                                    'package': raw, 'review': mine})
                continue
            theirs = float(raw)
            checked += 1
            # 交付表按固定小数位四舍五入，比较时取其小数位精度与绝对容差的较大者。
            decimals = len(raw.split('.')[1]) if '.' in raw else 0
            limit = 0.0 if column in INTEGER_COLUMNS else max(0.5 * 10 ** -decimals, abs(theirs) * tolerance)
            if abs(theirs - float(mine)) > limit:
                differences.append({'month': month, 'column': column, 'issue': 'value_mismatch',
                                    'package': theirs, 'review': float(mine),
                                    'difference': float(mine) - theirs, 'allowed': limit})
    quarantined = sorted(m for m, v in local.items() if v['status'] == 'quarantined')
    return {'months_in_package': len(delivered), 'months_in_review': len(local),
            'values_compared': checked, 'mismatch_count': len(differences),
            'differences': differences[:50],
            'review_quarantined_months': quarantined,
            'note': 'both sides independently computed; comparison does not grant scoring eligibility'}


def verify_checksums(package: Path) -> dict:
    """迁移后依据包内清单校验文件，不修改清单或执行交付脚本。"""
    package = package.resolve(strict=True)
    manifest = package / '04_复现与校验' / 'checksums.sha256'
    checked, mismatches, seen = 0, [], set()
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+(.+)', line)
        if not match:
            raise ValueError('校验清单格式无效')
        digest, relative = match.groups()
        path = (package / relative).resolve()
        if not path.is_relative_to(package) or path in seen:
            raise ValueError('校验路径越界或重复')
        seen.add(path)
        checked += 1
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest.lower():
            mismatches.append(relative)
    if not checked:
        raise ValueError('校验清单为空')
    return {'files_checked': checked, 'mismatches': mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--review-root', type=Path, required=True)
    parser.add_argument('--tolerance', type=float, default=5e-6)
    parser.add_argument('--checksums', action='store_true')
    args = parser.parse_args()
    report = compare(args.package.resolve(strict=True), args.review_root.resolve(strict=True), args.tolerance)
    checksums = verify_checksums(args.package) if args.checksums else None
    if checksums:
        report['checksums'] = checksums
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['mismatch_count'] == 0 and not (checksums or {}).get('mismatches') else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

"""显式选择存储；PostgreSQL 失败不允许偷偷退回文件。"""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from utils.data_contract import (RISK_VERSION, article_identity, business_date, canonical_url,
                                 fingerprint, json_safe, select_history, time_window, utc_now,
                                 valid_risk_record, timestamp_utc, RUN_MODES, source_evidence)


_task_scope = ContextVar('repository_task_scope', default=None)


def current_task():
    return _task_scope.get()


@contextmanager
def task_scope(queue, job, lease_lost):
    token = _task_scope.set((queue, job, lease_lost))
    try:
        yield
    finally:
        _task_scope.reset(token)


def get_repository():
    if current_task() is not None:
        return current_task()[0].repo
    from utils.config import get_storage_config
    backend = os.environ.get("STORAGE_BACKEND") or get_storage_config().get("backend", "file")
    if backend == "file":
        return None
    if backend != "postgres":
        raise ValueError("storage.backend 只能为 file 或 postgres")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("postgres 模式需要 DATABASE_URL；不会回退到文件")
    return repository_for_url(url)


@lru_cache(maxsize=4)
def repository_for_url(url):
    return PostgresRepository(url)


class PostgresRepository:
    def __init__(self, url):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql":
            raise ValueError("只支持 PostgreSQL/PostGIS；SQLite 不是集成测试替代品")
        from storage import schema
        self.schema = schema
        self.engine = create_engine(parsed.set(drivername="postgresql+psycopg"),
                                    pool_pre_ping=True, pool_size=5, max_overflow=5,
                                    connect_args={"connect_timeout": 5}, hide_parameters=True)

    def assert_task_write(self, conn):
        scope = current_task()
        if scope is not None:
            queue, job, lease_lost = scope
            if queue.repo is not self or conn.engine is not self.engine:
                raise RuntimeError('任务不能写入其他数据库连接')
            queue.assert_owned(conn, job, lease_lost)

    @contextmanager
    def write_transaction(self):
        """写事务持有任务行锁；提交前复查租约，失效则整体回滚本次写入。"""
        with self.engine.begin() as conn:
            self.assert_task_write(conn)
            yield conn
            self.assert_task_write(conn)

    @staticmethod
    def _insert(conn, table, values):
        from sqlalchemy.dialects.postgresql import insert
        conn.execute(insert(table).values(**values).on_conflict_do_nothing())

    @staticmethod
    def _upsert(conn, table, values, keys):
        from sqlalchemy.dialects.postgresql import insert
        query = insert(table).values(**values)
        conn.execute(query.on_conflict_do_update(index_elements=keys,
                     set_={k: getattr(query.excluded, k) for k in values if k not in keys and k != "id"}))

    def source(self, conn, name):
        name = str(name or "unknown")
        sid = fingerprint(name)
        self._insert(conn, self.schema.sources, {"id": sid, "name": name})
        return sid

    def save_collected_articles(self, items):
        stamp = utc_now().isoformat()
        return self.save_articles([{**row, 'run_kind': 'live',
            'collected_at': (timestamp_utc(row.get('collected_at')) or timestamp_utc(row.get('crawled_at'))
                             or timestamp_utc(stamp)).isoformat()} for row in items])

    def save_articles(self, items, conn=None):
        if conn is None:
            with self.write_transaction() as connection:
                return self.save_articles(items, connection)
        self.assert_task_write(conn)
        ids = []
        for item in items:
            if not isinstance(item, dict) or not (item.get("content") or item.get("title") or item.get("text")):
                raise ValueError("文章必须有非空标题或正文")
            aid = article_identity(item)
            sid = self.source(conn, item.get("source"))
            raw_time = item.get("published_at") or item.get("pub_time") or item.get("date")
            published = business_date(raw_time)
            stamp = timestamp_utc(raw_time)
            values = dict(id=aid, source_id=sid, external_id=str(item["external_id"]) if item.get("external_id") else None,
                          canonical_url=canonical_url(item.get("url")) or None, content_hash=aid,
                          title=str(item.get("title") or ""), content=str(item.get("content") or ""),
                          language=item.get("language"), business_date=published,
                          published_at=stamp, date_precision="timestamp" if stamp else "day" if published else "missing", payload=json_safe(item))
            if timestamp_utc(item.get("collected_at")):
                values["collected_at"] = timestamp_utc(item["collected_at"])
            self._insert(conn, self.schema.articles, values)
            from sqlalchemy import select, update
            table = self.schema.articles
            current = conn.execute(select(table).where(table.c.id == aid).with_for_update()).mappings().first()
            if current is None:
                raise ValueError("同来源外部ID出现不同正文，需人工确认版本，未覆盖原文")
            merged = dict(current["payload"])
            evidence = source_evidence({"source_evidence": source_evidence(merged) + source_evidence(item)})
            merged["source_evidence"] = evidence
            for field in ("sources", "source_urls"):
                previous, incoming = merged.get(field, []), item.get(field, [])
                merged[field] = sorted({v for values_list in (previous, incoming) if isinstance(values_list, list)
                                        for v in values_list if isinstance(v, str) and v})
            merged["sources"] = sorted(set(merged["sources"]) | {e["source"] for e in evidence})
            merged["source_urls"] = sorted(set(merged["source_urls"]) | {e["url"] for e in evidence if e["url"]})
            previous_stamp = timestamp_utc(merged.get('analyzed_at'))
            incoming_stamp = timestamp_utc(item.get('analyzed_at'))
            if (item.get('run_kind') in {'live', 'existing'} and incoming_stamp
                    and (previous_stamp is None or incoming_stamp >= previous_stamp)):
                for field in ('entities', 'ner_version', 'ner_backend', 'sentiment_score', 'sentiment_source',
                              'risk_sentiment', 'llm_analysis', 'analyzed_at', 'run_kind'):
                    if field in item:
                        merged[field] = item[field]
            updates = {}
            if published and (not current["business_date"] or published < current["business_date"]
                              or (published == current["business_date"] and stamp and current["published_at"] and stamp < current["published_at"])):
                updates = dict(business_date=published, published_at=stamp, date_precision=values["date_precision"])
                merged.update(pub_time=published.isoformat(), published_at=stamp.isoformat() if stamp else None,
                              date_precision=values["date_precision"], date_quality="observed")
            conn.execute(update(table).where(table.c.id == aid).values(payload=json_safe(merged), **updates))
            for entry in evidence:
                source_id = self.source(conn, entry["source"])
                self._insert(conn, self.schema.article_sources, dict(article_id=aid, source_id=source_id, url=entry["url"]))
            self.save_article_evidence(merged, conn)
            ids.append(aid)
        return ids

    def save_article_evidence(self, item, conn):
        """类型化保存实体提及及文章级共现，保留证据和算法版本。"""
        from analyzer.network_analyzer import build_article_graph
        day = business_date(item.get('published_at') or item.get('pub_time') or item.get('date'))
        if day is None or item.get('run_kind') not in {'live', 'existing'}:
            return
        graph = build_article_graph([item], days=1, end_date=day)
        aid, version = article_identity(item), item.get('ner_version', 'unknown')
        nodes = {n['id']: n for n in graph['nodes']}
        for nid, node in nodes.items():
            self._upsert(conn, self.schema.entity_mentions, {
                'id': fingerprint([aid, nid, version]), 'article_id': aid,
                'entity_name': node['name'], 'entity_type': node['entity_type'],
                'method': 'article_entity_mention', 'evidence': '正文级提及，未提供字符偏移',
                'payload': json_safe({'algorithm_version': version, 'sources': source_evidence(item)})}, ['id'])
        for edge in graph['edges']:
            self._upsert(conn, self.schema.relation_evidence, {
                'id': fingerprint([aid, edge['source'], edge['target'], version]), 'article_id': aid,
                'subject': nodes[edge['source']]['name'], 'object': nodes[edge['target']]['name'],
                'relation_type': 'CO_OCCURS_IN_ARTICLE', 'observed_date': day,
                'run_kind': item['run_kind'], 'evidence': '同一篇报道提及，不代表合作、冲突或因果',
                'payload': json_safe(edge)}, ['id'])

    def load_articles(self, date=None):
        from sqlalchemy import select
        table = self.schema.articles
        query = select(table.c.payload).order_by(table.c.business_date, table.c.id)
        if date:
            if business_date(date) is None:
                raise ValueError("无效发布日期")
            query = query.where(table.c.business_date == business_date(date))
        with self.engine.connect() as conn:
            return list(conn.execute(query).scalars())

    def save_analysis(self, result, conn=None):
        if conn is None:
            with self.write_transaction() as connection:
                return self.save_analysis(result, connection)
        self.assert_task_write(conn)
        rid = result.get("run_id") or uuid4().hex
        self._insert(conn, self.schema.analysis_runs, {
            "id": rid, "owner_id": result.get("owner_id"), "shared": bool(result.get("shared", False)),
            "run_kind": result.get("run_kind", "manual"), "algorithm_version": result.get("algorithm_version", RISK_VERSION),
            "input_hash": result.get("input_hash") or fingerprint(result),
            "status": result.get("status", "ok"), "payload": json_safe(result),
        })
        return rid

    def save_risk(self, record, conn=None):
        if not valid_risk_record(record) or record.get("run_kind", "legacy") not in RUN_MODES:
            raise ValueError("无效风险记录")
        record = dict(record)
        record.setdefault("run_kind", "legacy")
        record.setdefault("algorithm_version", "legacy")
        record["recorded_at"] = (timestamp_utc(record.get("recorded_at")) or utc_now()).isoformat()
        if not record.get("run_id"):
            stable = {k: v for k, v in record.items() if k not in {"recorded_at", "analyzed_at"}}
            record["run_id"] = fingerprint(stable if record.get("input_hash") else record)
        if conn is None:
            with self.write_transaction() as connection:
                return self.save_risk(record, connection)
        self.assert_task_write(conn)
        mode = record.get("run_kind", "legacy")
        if mode in {"live", "existing"} and record.get("input_hash"):
            from sqlalchemy import select, text
            group = {"date": business_date(record["date"]).isoformat(), "region": record.get("region", "MMR"),
                     "version": record["algorithm_version"]}
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(fingerprint(group)[:15], 16)})
            table = self.schema.daily_risk
            current = conn.execute(select(table.c.id, table.c.input_hash).where(
                table.c.date == business_date(record["date"]), table.c.region == group["region"],
                table.c.algorithm_version == group["version"], table.c.data_domain == "observed")).first()
            if current and current.input_hash == record["input_hash"]:
                return current.id
        run_id = self.save_analysis(record, conn)
        if mode in {"manual", "demo"}:
            return run_id
        domain = "legacy" if mode == "legacy" else "observed"
        keys = ["date", "region", "algorithm_version", "data_domain"]
        values = {
            "date": business_date(record["date"]), "region": record.get("region", "MMR"),
            "algorithm_version": record.get("algorithm_version", "legacy"), "data_domain": domain,
            "run_kind": mode, "risk_score": record["risk_score"], "sample_count": record.get("sample_count", 0),
            "input_hash": record.get("input_hash"), "run_id": run_id,
            "recorded_at": timestamp_utc(record["recorded_at"]), "payload": json_safe(record),
        }
        values["id"] = fingerprint({key: values[key] for key in keys})
        from sqlalchemy.dialects.postgresql import insert
        table = self.schema.daily_risk
        stmt = insert(table).values(**values)
        conn.execute(stmt.on_conflict_do_update(index_elements=keys,
                     set_={k: getattr(stmt.excluded, k) for k in values if k not in keys and k != "id"},
                     where=table.c.recorded_at < stmt.excluded.recorded_at))
        return values["id"]

    def load_risk_history(self, days=30, end_date=None, include_legacy=False, algorithm_version=RISK_VERSION, region="MMR"):
        from sqlalchemy import select
        start, end = time_window(days, end_date)
        table = self.schema.daily_risk
        query = select(table.c.payload).where(table.c.date >= start, table.c.date < end, table.c.region == region,
                                              table.c.data_domain == ("legacy" if include_legacy else "observed"))
        if not include_legacy:
            query = query.where(table.c.algorithm_version == algorithm_version)
        with self.engine.connect() as conn:
            rows = list(conn.execute(query.order_by(table.c.date)).scalars())
        return select_history(rows, days, end_date, include_legacy, algorithm_version, region)

    def save_events(self, rows, watermark=None, source="gdelt", conn=None):
        """事件与连续成功水位在同一事务提交，失败全部回滚。"""
        if conn is None:
            with self.write_transaction() as connection:
                return self.save_events(rows, watermark, source, connection)
        self.assert_task_write(conn)
        ids = []
        sid = self.source(conn, source)
        for row in rows:
            day = business_date(row.get("date") or row.get("sqldate") or "")
            if not day:
                raise ValueError("事件缺少有效日期，不能推进该批次水位")
            external_id = str(row.get("event_id") or row.get("global_event_id") or "")
            eid = fingerprint({"source": source, "id": external_id}) if external_id else fingerprint(row)
            lat, lon = row.get("lat"), row.get("lon")
            from utils.data_contract import finite_number
            geom = f"SRID=4326;POINT({lon} {lat})" if finite_number(lat) and finite_number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 else None
            self._upsert(conn, self.schema.events, dict(id=eid, source_id=sid,
                         external_id=f"{source}:{external_id}" if external_id else None,
                         event_date=day, event_type=str(row.get("event_code", "")), geom=geom,
                         severity=row.get("severity") if finite_number(row.get("severity")) else None,
                         event_time=timestamp_utc(row.get("event_time")), updated_at=utc_now(),
                         run_kind=row.get("run_kind", "existing"), payload=json_safe(row)), ["id"])
            ids.append(eid)
        if watermark is not None:
            try:
                if len(str(watermark)) != 14:
                    raise ValueError()
                datetime.strptime(str(watermark), "%Y%m%d%H%M%S")
            except ValueError:
                raise ValueError("水位必须为有效的14位UTC批次时间") from None
            from sqlalchemy.dialects.postgresql import insert
            table = self.schema.ingestion_checkpoints
            stmt = insert(table).values(source=source, watermark=str(watermark))
            conn.execute(stmt.on_conflict_do_update(index_elements=["source"],
                         set_={"watermark": stmt.excluded.watermark, "updated_at": utc_now()},
                         where=table.c.watermark < stmt.excluded.watermark))
        return ids

    def load_events(self, days=30, end_date=None):
        from sqlalchemy import select
        table = self.schema.events
        query = select(table.c.payload).where(table.c.run_kind.in_(["live", "existing"]))
        if days is not None:
            start, end = time_window(days, end_date)
            query = query.where(table.c.event_date >= start, table.c.event_date < end)
        with self.engine.connect() as conn:
            return list(conn.execute(query.order_by(table.c.event_date, table.c.id)).scalars())

    def save_observations(self, rows, conn=None):
        from utils.data_contract import normalize_observation
        from sqlalchemy import select
        if conn is None:
            with self.write_transaction() as connection:
                return self.save_observations(rows, connection)
        self.assert_task_write(conn)
        ids = []
        t = self.schema.indicator_observations
        for raw in rows:
            row = normalize_observation(raw)
            values = {key: row[key] for key in ('id', 'indicator', 'region', 'frequency', 'value',
                      'unit', 'quality', 'dataset_version', 'product', 'run_kind')}
            values.update(source_id=self.source(conn, row['source']),
                          period_start=business_date(row['period_start']),
                          period_end=business_date(row['period_end']), payload=row)
            self._insert(conn, t, values)
            current = conn.execute(select(t).where(t.c.id == row['id']).with_for_update()).mappings().first()
            if current is None or fingerprint(current['payload']) != fingerprint(row):
                raise ValueError('观测ID或同源周期版本冲突；未覆盖已有值，请复核原始证据')
            if any(current[key] != value for key, value in values.items() if key != 'payload'):
                raise ValueError('旧观测类型化字段尚未复核；请保留旧行，以经复核的新ID及版本重新导入')
            ids.append(row['id'])
        return ids

    def save_source_run(self, source, entry):
        with self.write_transaction() as conn:
            sid = self.source(conn, source)
            self._insert(conn, self.schema.source_runs, {
                'id': uuid4().hex, 'source_id': sid,
                'status': 'ok' if entry['ok'] else 'failed', 'record_count': entry['count'],
                'payload': json_safe(entry)})

    def load_source_runs(self, limit=20):
        from sqlalchemy import select, func
        t, sources = self.schema.source_runs, self.schema.sources
        ranked = select(t.c.source_id, t.c.payload, t.c.created_at, t.c.id,
                        func.row_number().over(partition_by=t.c.source_id,
                        order_by=(t.c.created_at.desc(), t.c.id.desc())).label('rank')).where(
                        t.c.status.in_(['ok', 'failed'])).subquery()
        query = select(sources.c.name, ranked.c.payload).join_from(ranked, sources,
                ranked.c.source_id == sources.c.id).where(ranked.c.rank <= limit).order_by(
                ranked.c.created_at, ranked.c.id)
        result = {}
        with self.engine.connect() as conn:
            for name, payload in conn.execute(query):
                result.setdefault(name, []).append(payload)
        return result

    def checkpoint(self, source="gdelt"):
        from sqlalchemy import select
        table = self.schema.ingestion_checkpoints
        with self.engine.connect() as conn:
            return conn.execute(select(table.c.watermark).where(table.c.source == source)).scalar_one_or_none()

    def revision(self):
        from sqlalchemy import text
        with self.engine.connect() as conn:
            value = conn.execute(text('SELECT revision FROM data_revision WHERE id = 1')).scalar_one()
        return fingerprint(['postgres-data-revision-v1', value])

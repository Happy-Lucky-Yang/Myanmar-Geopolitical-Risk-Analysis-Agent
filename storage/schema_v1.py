"""不可变的首版 Schema 快照；后续结构变更必须通过新 Alembic revision。"""
from sqlalchemy import (MetaData, Table, Column as C, String, Text, Integer, Float,
                        Boolean, Date, DateTime, ForeignKey as FK, UniqueConstraint as UQ,
                        CheckConstraint as CK, Index, text)
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry

metadata = MetaData()


def identity():
    return C("id", String(64), primary_key=True)


def timestamp(name="created_at"):
    return C(name, DateTime(timezone=True), nullable=False, server_default=text("now()"))


def payload(name="payload"):
    return C(name, JSONB, nullable=False, server_default=text("'{}'::jsonb"))


sources = Table("sources", metadata, identity(), C("name", Text, nullable=False, unique=True),
                C("license", Text), timestamp())
users = Table("users", metadata, identity(), C("username", String(128), nullable=False, unique=True),
              C("password_hash", Text, nullable=False), C("role", String(16), nullable=False),
              C("active", Boolean, nullable=False, server_default=text("true")), timestamp(),
              CK("role IN ('reader','analyst','admin')", name="user_role"))
articles = Table("articles", metadata, identity(), C("source_id", String(64), FK("sources.id")),
                 C("external_id", Text), C("canonical_url", Text), C("content_hash", String(64), nullable=False, unique=True),
                 C("title", Text, nullable=False), C("content", Text), C("language", String(12)),
                 C("published_at", DateTime(timezone=True)), C("business_date", Date, index=True),
                 C("date_precision", String(16)), timestamp("collected_at"), payload(),
                 UQ("source_id", "external_id", name="article_external"))
article_sources = Table("article_sources", metadata, C("article_id", String(64), FK("articles.id"), primary_key=True),
                        C("source_id", String(64), FK("sources.id"), primary_key=True),
                        C("url", Text, primary_key=True), timestamp())
regions = Table("regions", metadata, identity(), C("code", String(64), nullable=False),
                C("name", Text, nullable=False), C("boundary_version", String(64), nullable=False),
                C("geom", Geometry("MULTIPOLYGON", srid=4326), nullable=False),
                C("license", Text), payload(), UQ("code", "boundary_version", name="region_version"))
events = Table("events", metadata, identity(), C("external_id", Text, unique=True),
               C("source_id", String(64), FK("sources.id")), C("event_date", Date, nullable=False, index=True),
               C("event_time", DateTime(timezone=True)), C("event_type", Text), C("severity", Float),
               C("geom", Geometry("POINT", srid=4326)), C("region_id", String(64), FK("regions.id")),
               C("run_kind", String(16), nullable=False, server_default="legacy"), timestamp(), timestamp("updated_at"), payload(),
               CK("run_kind IN ('live','existing','manual','demo','legacy')", name="event_run_kind"))
event_evidence = Table("event_evidence", metadata, identity(), C("event_id", String(64), FK("events.id"), nullable=False),
                       C("article_id", String(64), FK("articles.id")), C("url", Text), C("method", Text),
                       UQ("event_id", "article_id", name="event_article_evidence"), payload())
entity_mentions = Table("entity_mentions", metadata, identity(), C("article_id", String(64), FK("articles.id"), nullable=False),
                        C("entity_name", Text, nullable=False), C("entity_type", String(32)),
                        C("method", Text), C("evidence", Text), payload())
relation_evidence = Table("relation_evidence", metadata, identity(), C("article_id", String(64), FK("articles.id")),
                          C("subject", Text, nullable=False), C("object", Text, nullable=False),
                          C("relation_type", String(64), nullable=False), C("observed_date", Date),
                          C("run_kind", String(16), nullable=False), C("evidence", Text), payload())
analysis_runs = Table("analysis_runs", metadata, identity(), C("owner_id", String(64), FK("users.id")),
                      C("shared", Boolean, nullable=False, server_default=text("false")),
                      C("run_kind", String(16), nullable=False), C("algorithm_version", String(64), nullable=False),
                      C("input_hash", String(64), index=True), C("status", String(16), nullable=False), timestamp(), payload(),
                      CK("run_kind IN ('live','existing','manual','demo','legacy')", name="analysis_run_kind"))
indicator_observations = Table("indicator_observations", metadata, identity(),
                               C("source_id", String(64), FK("sources.id")), C("indicator", String(64), nullable=False),
                               C("region", String(64), nullable=False), C("period_start", Date, nullable=False),
                               C("frequency", String(16), nullable=False), C("value", Float), C("unit", Text),
                               C("quality", String(16), nullable=False), timestamp(), payload(),
                               UQ("source_id", "indicator", "region", "period_start", "frequency", name="indicator_period"))
daily_risk = Table("daily_risk", metadata, identity(), C("date", Date, nullable=False, index=True),
                   C("region", String(64), nullable=False), C("algorithm_version", String(64), nullable=False),
                   C("data_domain", String(16), nullable=False), C("run_kind", String(16), nullable=False),
                   C("risk_score", Float, nullable=False), C("sample_count", Integer, nullable=False),
                   C("input_hash", String(64)), C("run_id", String(64), FK("analysis_runs.id")), timestamp("recorded_at"), payload(),
                   CK("risk_score >= 0 AND risk_score <= 100", name="risk_score_range"),
                   CK("sample_count >= 0", name="risk_sample_count"),
                   CK("(data_domain = 'observed' AND run_kind IN ('live','existing')) OR (data_domain = 'legacy' AND run_kind = 'legacy')", name="risk_domain"),
                   UQ("date", "region", "algorithm_version", "data_domain", name="daily_risk_version"))
source_runs = Table("source_runs", metadata, identity(), C("source_id", String(64), FK("sources.id")),
                    C("status", String(16), nullable=False), C("record_count", Integer), timestamp(), payload())
jobs = Table("jobs", metadata, identity(), C("kind", String(32), nullable=False), C("owner_id", String(64), FK("users.id")),
             C("idempotency_key", String(128), nullable=False, unique=True),
             C("status", String(16), nullable=False, server_default="queued"), C("attempts", Integer, nullable=False, server_default="0"),
             C("max_attempts", Integer, nullable=False, server_default="3"), C("lease_token", String(64)),
             C("lease_until", DateTime(timezone=True)), timestamp("available_at"), timestamp(), timestamp("updated_at"),
             payload(), payload("result"), C("error", Text),
             CK("status IN ('queued','running','done','failed')", name="job_status"))
Index("ix_jobs_claim", jobs.c.status, jobs.c.available_at)
ingestion_checkpoints = Table("ingestion_checkpoints", metadata, C("source", String(64), primary_key=True),
                              C("watermark", String(128), nullable=False), timestamp("updated_at"))
alerts = Table("alerts", metadata, identity(), C("level", String(16), nullable=False),
               C("date", Date, nullable=False), C("region", String(64), nullable=False),
               C("algorithm_version", String(64), nullable=False), C("acknowledged_by", String(64), FK("users.id")),
               C("acknowledged_at", DateTime(timezone=True)), timestamp(), payload(),
               UQ("date", "region", "algorithm_version", "level", name="alert_dedup"))
audit_logs = Table("audit_logs", metadata, identity(), C("user_id", String(64), FK("users.id")),
                   C("action", String(64), nullable=False), C("target", String(128)), timestamp(), payload())
import_batches = Table("import_batches", metadata, identity(), C("file_hash", String(64), nullable=False),
                       C("file_name", Text, nullable=False), C("status", String(16), nullable=False), timestamp(), payload(),
                       UQ("file_hash", "file_name", name="import_file"))
import_items = Table("import_items", metadata, identity(), C("batch_id", String(64), FK("import_batches.id"), nullable=False),
                     C("row_number", Integer, nullable=False), C("status", String(16), nullable=False),
                     C("record_id", String(64)), C("error", Text), UQ("batch_id", "row_number", name="import_row"))

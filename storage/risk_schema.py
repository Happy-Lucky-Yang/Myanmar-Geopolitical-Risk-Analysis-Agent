"""多源面板新增结构；不修改冻结schema，不在启动建表。"""
from sqlalchemy import Table, Column, String, DateTime, CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

TABLES = ('risk_source_batches', 'risk_calibrations', 'risk_panels', 'event_links')


def attach(metadata):
    if 'risk_panels' in metadata.tables:
        return
    for name in TABLES:
        columns = [Column('id', String(64), primary_key=True),
                   Column('payload', JSONB, nullable=False),
                   Column('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()'))]
        if name == 'risk_source_batches':
            columns += [Column('source_version_key', String(64), nullable=False, unique=True)]
        if name == 'risk_panels':
            columns += [Column('frequency', String(16), nullable=False), Column('region', String(64), nullable=False),
                Column('algorithm_version', String(64), nullable=False),
                CheckConstraint("frequency IN ('monthly','annual')", name='risk_panel_frequency')]
        if name == 'event_links':
            columns += [Column('reviewer_id', String(64), ForeignKey('users.id'))]
        Table(name, metadata, *columns)

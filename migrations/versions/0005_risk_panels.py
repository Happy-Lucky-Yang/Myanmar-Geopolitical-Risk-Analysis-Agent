"""新增隔离面板表；合并前必须由部署负责人核对迁移head。"""
from alembic import op, context
from sqlalchemy import MetaData, Table, Column, String
from storage.risk_schema import attach, TABLES

revision = '0005_risk_panels'
down_revision = '0004_workspace'
branch_labels = None
depends_on = None


def tables():
    metadata = MetaData()
    Table('users', metadata, Column('id', String(64), primary_key=True))
    attach(metadata)
    return [t for t in metadata.sorted_tables if t.name in TABLES]


def upgrade():
    for t in tables():
        t.create(op.get_bind(), checkfirst=False)


def downgrade():
    if context.get_x_argument(as_dictionary=True).get('confirm_drop') != 'risk_panels':
        raise RuntimeError('风险面板数据降级需要先备份并显式确认risk_panels')
    for t in reversed(tables()):
        t.drop(op.get_bind(), checkfirst=False)

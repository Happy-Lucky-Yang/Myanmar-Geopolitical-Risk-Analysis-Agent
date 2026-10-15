"""新增个人工作区；不重建已有账户或分析表。"""
from alembic import op, context
from sqlalchemy import MetaData, Table, Column, String
from storage.workspace_schema import attach, TABLES

revision = '0004_workspace'
down_revision = '0003_observation_versions'
branch_labels = None
depends_on = None


def tables():
    metadata = MetaData()
    Table('users', metadata, Column('id', String(64), primary_key=True))
    attach(metadata)
    return [t for t in metadata.sorted_tables if t.name in TABLES]


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=False)


def downgrade():
    if context.get_x_argument(as_dictionary=True).get('confirm_drop') != 'workspace_tables':
        raise RuntimeError('降级会删除个人工作区数据，须先备份并显式确认 workspace_tables')
    for table in reversed(tables()):
        table.drop(op.get_bind(), checkfirst=False)

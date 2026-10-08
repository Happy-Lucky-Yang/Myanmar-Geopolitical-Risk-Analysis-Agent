"""事务内统一数据修订号，覆盖文章、证据、事件、观测与日指标。"""
from alembic import op, context
import sqlalchemy as sa

revision = '0002_data_revision'
down_revision = '0001_postgis'
branch_labels = None
depends_on = None

TABLES = ('sources', 'articles', 'article_sources', 'regions', 'events', 'event_evidence',
          'entity_mentions', 'relation_evidence', 'indicator_observations', 'daily_risk', 'source_runs')


def schema_name():
    dialect = op.get_context().dialect
    return dialect.identifier_preparer.quote_schema(dialect.default_schema_name or 'public')


def upgrade():
    schema = schema_name()
    op.create_table('data_revision', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('revision', sa.BigInteger(), nullable=False),
                    sa.CheckConstraint('id = 1', name='single_data_revision'))
    op.execute(f'INSERT INTO {schema}.data_revision (id, revision) VALUES (1, 0)')
    op.execute(f'''CREATE FUNCTION {schema}.bump_data_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        UPDATE {schema}.data_revision SET revision = revision + 1 WHERE id = 1;
        RETURN NULL; END; $$''')
    for table in TABLES:
        op.execute(f'''CREATE TRIGGER revision_after_change
            AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {schema}.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION {schema}.bump_data_revision()''')


def downgrade():
    if context.get_x_argument(as_dictionary=True).get('confirm_drop') != 'business_tables':
        raise RuntimeError('移除修订号会使缓存契约失效，需显式确认降级')
    schema = schema_name()
    for table in TABLES:
        op.execute(f'DROP TRIGGER revision_after_change ON {schema}.{table}')
    op.execute(f'DROP FUNCTION {schema}.bump_data_revision()')
    op.drop_table('data_revision')

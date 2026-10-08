"""观测周期、运行域和产品版本类型化，保留同源的不同版本。"""
from alembic import op, context
import sqlalchemy as sa

revision = '0003_observation_versions'
down_revision = '0002_data_revision'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('indicator_observations', sa.Column('period_end', sa.Date()))
    op.add_column('indicator_observations', sa.Column('dataset_version', sa.String(128), nullable=False, server_default='legacy'))
    op.add_column('indicator_observations', sa.Column('product', sa.Text(), nullable=False, server_default=''))
    op.add_column('indicator_observations', sa.Column('run_kind', sa.String(16), nullable=False, server_default='legacy'))
    op.drop_constraint('indicator_period', 'indicator_observations', type_='unique')
    op.create_unique_constraint('indicator_series_period', 'indicator_observations',
        ['source_id', 'indicator', 'region', 'period_start', 'frequency', 'product', 'dataset_version'])
    op.create_check_constraint('observation_run_kind', 'indicator_observations',
        "run_kind IN ('live','existing','manual','demo','legacy')")
    op.create_check_constraint('observation_period', 'indicator_observations',
        'period_end IS NULL OR period_end > period_start')


def downgrade():
    if context.get_x_argument(as_dictionary=True).get('confirm_drop') != 'business_tables':
        raise RuntimeError('观测降级将移除周期和版本字段，需要显式确认；不是无损回退工具')
    op.drop_constraint('observation_period', 'indicator_observations', type_='check')
    op.drop_constraint('observation_run_kind', 'indicator_observations', type_='check')
    op.drop_constraint('indicator_series_period', 'indicator_observations', type_='unique')
    op.create_unique_constraint('indicator_period', 'indicator_observations',
        ['source_id', 'indicator', 'region', 'period_start', 'frequency'])
    for name in ('run_kind', 'product', 'dataset_version', 'period_end'):
        op.drop_column('indicator_observations', name)

"""当前运行时结构；复制冻结首版，结构升级由 Alembic 独立版本执行。"""
from sqlalchemy import MetaData, Table, Column, Date, String, Text, Integer, BigInteger, UniqueConstraint, CheckConstraint
from storage import schema_v1

metadata = MetaData()
for name, table in schema_v1.metadata.tables.items():
    globals()[name] = table.to_metadata(metadata)

data_revision = Table('data_revision', metadata, Column('id', Integer, primary_key=True),
    Column('revision', BigInteger, nullable=False), CheckConstraint('id = 1', name='single_data_revision'))

observations = metadata.tables['indicator_observations']
for constraint in list(observations.constraints):
    if constraint.name == 'indicator_period':
        observations.constraints.remove(constraint)
observations.append_column(Column('period_end', Date))
observations.append_column(Column('dataset_version', String(128), nullable=False, server_default='legacy'))
observations.append_column(Column('product', Text, nullable=False, server_default=''))
observations.append_column(Column('run_kind', String(16), nullable=False, server_default='legacy'))
observations.append_constraint(UniqueConstraint('source_id', 'indicator', 'region', 'period_start',
    'frequency', 'product', 'dataset_version', name='indicator_series_period'))
observations.append_constraint(CheckConstraint("run_kind IN ('live','existing','manual','demo','legacy')", name='observation_run_kind'))
observations.append_constraint(CheckConstraint('period_end IS NULL OR period_end > period_start', name='observation_period'))

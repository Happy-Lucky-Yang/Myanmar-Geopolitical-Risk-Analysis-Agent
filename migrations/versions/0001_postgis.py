"""首版业务库与空间索引。"""
from alembic import op, context
from storage.schema_v1 import metadata

revision = "0001_postgis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade():
    if context.get_x_argument(as_dictionary=True).get("confirm_drop") != "business_tables":
        raise RuntimeError("降级将移除业务表；需备份核验并显式 -x confirm_drop=business_tables")
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)

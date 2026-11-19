from alembic import op
import sqlalchemy as sa

revision = "0002_flow_draft"
down_revision = "0001_campaign_setup"


def upgrade():
    op.create_table(
        "flow_draft",
        sa.Column("flow_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("published", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade():
    op.drop_table("flow_draft")

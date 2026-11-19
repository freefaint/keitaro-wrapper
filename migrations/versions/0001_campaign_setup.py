from alembic import op
import sqlalchemy as sa

revision = "0001_campaign_setup"
down_revision = None


def upgrade():
    op.create_table(
        "campaign_setup",
        sa.Column("alias", sa.String(), primary_key=True),
        sa.Column("remote_id", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("campaign_setup")

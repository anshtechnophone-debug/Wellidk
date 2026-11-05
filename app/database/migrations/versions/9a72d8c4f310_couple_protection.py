"""Add daily couple protection points."""
from alembic import op
import sqlalchemy as sa

revision = "9a72d8c4f310"
down_revision = "4fdd6985c058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "couple_protections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("couple_left_id", sa.BigInteger(), nullable=False),
        sa.Column("couple_right_id", sa.BigInteger(), nullable=False),
        sa.Column("protector_id", sa.BigInteger(), nullable=False),
        sa.Column("protected_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("protection_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "couple_left_id", "couple_right_id", "protector_id", "protection_date",
            name="uq_couple_protection_daily",
        ),
    )
    op.create_index(
        "ix_couple_protection_pair", "couple_protections",
        ["couple_left_id", "couple_right_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_couple_protection_pair", table_name="couple_protections")
    op.drop_table("couple_protections")

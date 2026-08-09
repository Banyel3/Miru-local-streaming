"""Watch Now streams; only Keep fills the library

The user pressed Watch Now and found the whole film in their library.
BitTorrent downloads everything either way — sequential changes piece order,
not how much — so the streaming that is actually buildable is the Stremio
model: the disk is the buffer, nothing is promoted unless kept, and a janitor
deletes what nobody kept.

ephemeral marks the grab; download_name is the filename the downloader wrote
(the mover's skip-list is built from it); last_streamed_at is what the janitor
ages against.

Revision ID: a9c4e71f3b25
Revises: f2b8d47e1a09
"""

import sqlalchemy as sa
from alembic import op

revision = "a9c4e71f3b25"
down_revision = "f2b8d47e1a09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_works",
        sa.Column("ephemeral", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("catalog_works", sa.Column("download_name", sa.String(512), nullable=True))
    op.add_column(
        "catalog_works",
        sa.Column("last_streamed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_works", "last_streamed_at")
    op.drop_column("catalog_works", "download_name")
    op.drop_column("catalog_works", "ephemeral")

"""series identity: a work is a provider id, not a title

Adds the resolution cache, the provider identity key, and the two fields that
tell a film from a weekly show.

The data cleanup has to happen before the key can exist. The live catalogue held
21 works with no releases at all — ghosts that `_restate_works` had zeroed and
the rails were already hiding, but which still owned the unique title their
replacement needed — and one pair of works already resolved to the same provider
id, which is the duplicate the new constraint forbids.

Postgres only, like the deployment. The test suite builds its schema from the
models rather than replaying migrations.

Revision ID: b8a1ba51103b
Revises: 17da225cf72e
Create Date: 2026-08-08 12:07:04.710469
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b8a1ba51103b'
down_revision = '17da225cf72e'
branch_labels = None
depends_on = None


# The surviving card of a duplicated provider id: the one holding the most
# releases, so the merge moves as little as possible.
_WINNERS = """
WITH winner AS (
    SELECT DISTINCT ON (kind, provider, provider_id)
           kind, provider, provider_id, id
      FROM catalog_works
     WHERE provider IS NOT NULL AND provider <> 'none'
     ORDER BY kind, provider, provider_id, release_count DESC, id
)
"""


def upgrade() -> None:
    op.create_table(
        'catalog_title_resolutions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('query', sa.String(length=512), nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=True),
        sa.Column('provider_id', sa.String(length=64), nullable=True),
        sa.Column(
            'data',
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
            nullable=False,
        ),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'query', name='uq_resolution_query'),
    )
    op.add_column('catalog_works', sa.Column('format', sa.String(length=16), nullable=True))
    op.add_column('catalog_works', sa.Column('episode_count', sa.Integer(), nullable=True))

    # Releases move to the surviving card first — they are the catalogue, and
    # the accumulate rule means they cannot be re-fetched.
    op.execute(_WINNERS + """
        UPDATE catalog_releases r
           SET work_id = winner.id
          FROM catalog_works w
          JOIN winner USING (kind, provider, provider_id)
         WHERE r.work_id = w.id AND w.id <> winner.id
    """)
    # A download in flight on a losing card would otherwise be forgotten, and
    # the card that inherits its releases would offer to download it again.
    op.execute(_WINNERS + """
        UPDATE catalog_works k
           SET library_file_id = COALESCE(k.library_file_id, loser.library_file_id),
               download_job_id = COALESCE(k.download_job_id, loser.download_job_id)
          FROM winner
          JOIN catalog_works loser USING (kind, provider, provider_id)
         WHERE k.id = winner.id AND loser.id <> winner.id
    """)
    op.execute(_WINNERS + """
        DELETE FROM catalog_works w
         USING winner
         WHERE (w.kind, w.provider, w.provider_id)
             = (winner.kind, winner.provider, winner.provider_id)
           AND w.id <> winner.id
    """)

    # Ghosts. Kept only while they might still be a download's only record.
    op.execute("""
        DELETE FROM catalog_works w
         WHERE w.library_file_id IS NULL
           AND w.download_job_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM catalog_releases r WHERE r.work_id = w.id)
    """)

    op.create_unique_constraint(
        'uq_work_provider', 'catalog_works', ['kind', 'provider', 'provider_id']
    )


def downgrade() -> None:
    # The merges are not undone: the releases they moved are the catalogue, and
    # splitting them back apart would need the titles the merge deleted.
    op.drop_constraint('uq_work_provider', 'catalog_works', type_='unique')
    op.drop_column('catalog_works', 'episode_count')
    op.drop_column('catalog_works', 'format')
    op.drop_table('catalog_title_resolutions')

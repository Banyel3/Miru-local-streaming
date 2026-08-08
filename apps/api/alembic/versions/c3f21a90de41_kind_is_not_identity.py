"""kind is not identity

The indexer's Torznab categories decide a release's kind, and the indexers do
not agree with each other. Measured on the live catalogue for one show:

    series   The Pirate Bay   88 releases    (carries no anime tag at all)
    anime    Nyaa.si          43             (5070 TV/Anime)

`kind` then chose the metadata provider and sat inside both identity keys, so
one show held two provider ids that could never merge. Frieren was twelve cards.

Two changes here:

- The title-resolution cache is keyed on the query alone. Under (kind, query)
  the identical question was asked twice and could be answered differently each
  time, which is what produced the two ids. 15 titles in the live database are
  cached twice; the duplicates are collapsed, keeping the row that actually
  resolved.
- A work's provider identity is (provider, provider_id) without kind, since the
  kind is now derived from the provider rather than being an independent fact.
  TMDB ids are namespaced `movie:` / `tv:` in the same release, because TMDB
  numbers films and television separately and the id alone is not unique.

Revision ID: c3f21a90de41
Revises: b8a1ba51103b
"""

from alembic import op
import sqlalchemy as sa

revision = "c3f21a90de41"
down_revision = "b8a1ba51103b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the row that found something. A miss cached under the kind that could
    # not answer — series asking TVmaze about an anime — must not survive in
    # place of the hit recorded under the kind that could.
    op.execute(
        """
        DELETE FROM catalog_title_resolutions a
        USING catalog_title_resolutions b
        WHERE a.query = b.query
          AND a.id <> b.id
          AND (
                (a.provider IS NULL AND b.provider IS NOT NULL)
             OR (((a.provider IS NULL) = (b.provider IS NULL)) AND a.id > b.id)
          )
        """
    )
    op.drop_constraint("uq_resolution_query", "catalog_title_resolutions", type_="unique")
    op.create_unique_constraint(
        "uq_resolution_query", "catalog_title_resolutions", ["query"]
    )

    # Namespace the TMDB ids already stored, so they match what the fetcher
    # writes from now on and are not re-resolved as strangers.
    op.execute(
        """
        UPDATE catalog_works SET provider_id =
            CASE WHEN kind = 'movie' THEN 'movie:' ELSE 'tv:' END || provider_id
        WHERE provider = 'tmdb' AND provider_id NOT LIKE '%:%'
        """
    )
    op.execute(
        """
        UPDATE catalog_title_resolutions SET provider_id =
            CASE WHEN kind = 'movie' THEN 'movie:' ELSE 'tv:' END || provider_id
        WHERE provider = 'tmdb' AND provider_id IS NOT NULL AND provider_id NOT LIKE '%:%'
        """
    )

    # The cached `data` blob carries its own copy of provider_id, and it is the
    # copy `apply()` actually writes onto a work. Namespacing only the column
    # left the two disagreeing, so a resolved title looked unresolved to
    # work_by_provider and the fallback renamed a work into a collision.
    op.execute(
        """
        UPDATE catalog_title_resolutions
        SET data = jsonb_set(
                data::jsonb, '{provider_id}',
                to_jsonb((CASE WHEN kind = 'movie' THEN 'movie:' ELSE 'tv:' END)
                         || (data->>'provider_id'))
            )::json
        WHERE provider = 'tmdb'
          AND data ? 'provider_id'
          AND data->>'provider_id' NOT LIKE '%:%'
        """
    )

    op.drop_constraint("uq_work_provider", "catalog_works", type_="unique")
    op.create_unique_constraint(
        "uq_work_provider", "catalog_works", ["provider", "provider_id"]
    )


def downgrade() -> None:
    # The cached `data` blob carries its own copy of provider_id, and it is the
    # copy `apply()` actually writes onto a work. Namespacing only the column
    # left the two disagreeing, so a resolved title looked unresolved to
    # work_by_provider and the fallback renamed a work into a collision.
    op.execute(
        """
        UPDATE catalog_title_resolutions
        SET data = jsonb_set(
                data::jsonb, '{provider_id}',
                to_jsonb((CASE WHEN kind = 'movie' THEN 'movie:' ELSE 'tv:' END)
                         || (data->>'provider_id'))
            )::json
        WHERE provider = 'tmdb'
          AND data ? 'provider_id'
          AND data->>'provider_id' NOT LIKE '%:%'
        """
    )

    op.drop_constraint("uq_work_provider", "catalog_works", type_="unique")
    op.create_unique_constraint(
        "uq_work_provider", "catalog_works", ["kind", "provider", "provider_id"]
    )
    op.drop_constraint("uq_resolution_query", "catalog_title_resolutions", type_="unique")
    op.create_unique_constraint(
        "uq_resolution_query", "catalog_title_resolutions", ["kind", "query"]
    )

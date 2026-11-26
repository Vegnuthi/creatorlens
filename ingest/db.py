import psycopg
from pgvector.psycopg import register_vector

from .config import DATABASE_URL


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    conn = psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None)
    register_vector(conn)
    return conn


def upsert_creator(conn, c: dict) -> None:
    conn.execute(
        """
        insert into creators (channel_id, handle, title, description, country, default_language,
                              thumbnail_url, subscriber_count, view_count, video_count, published_at, last_synced_at)
        values (%(channel_id)s, %(handle)s, %(title)s, %(description)s, %(country)s, %(default_language)s,
                %(thumbnail_url)s, %(subscriber_count)s, %(view_count)s, %(video_count)s, %(published_at)s, now())
        on conflict (channel_id) do update set
            handle = excluded.handle, title = excluded.title, description = excluded.description,
            country = excluded.country, default_language = coalesce(excluded.default_language, creators.default_language),
            thumbnail_url = excluded.thumbnail_url, subscriber_count = excluded.subscriber_count,
            view_count = excluded.view_count, video_count = excluded.video_count, last_synced_at = now()
        """,
        c,
    )
    conn.execute(
        """
        insert into creator_snapshots (channel_id, snapshot_date, subscriber_count, view_count, video_count)
        values (%(channel_id)s, current_date, %(subscriber_count)s, %(view_count)s, %(video_count)s)
        on conflict (channel_id, snapshot_date) do update set
            subscriber_count = excluded.subscriber_count, view_count = excluded.view_count,
            video_count = excluded.video_count
        """,
        c,
    )


def upsert_videos(conn, videos: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into videos (video_id, channel_id, title, description, published_at, duration_seconds,
                                view_count, like_count, comment_count)
            values (%(video_id)s, %(channel_id)s, %(title)s, %(description)s, %(published_at)s,
                    %(duration_seconds)s, %(view_count)s, %(like_count)s, %(comment_count)s)
            on conflict (video_id) do update set
                title = excluded.title, description = excluded.description,
                view_count = excluded.view_count, like_count = excluded.like_count,
                comment_count = excluded.comment_count
            """,
            videos,
        )


def videos_to_index(conn, video_ids: list[str], retry_missing: bool = False) -> set[str]:
    """New videos, plus (with retry_missing) videos indexed earlier without captions."""
    rows = conn.execute(
        """
        select v from unnest(%s::text[]) as v
        where not exists (select 1 from content_chunks c where c.video_id = v)
           or (%s and exists (select 1 from videos x where x.video_id = v
                               and coalesce(x.transcript_status, 'none') = 'none'))
        """,
        (video_ids, retry_missing),
    ).fetchall()
    return {r[0] for r in rows}


def save_chunks(conn, video_id: str, channel_id: str, source: str, chunks: list[dict],
                embeddings, transcript_lang: str | None, transcript_status: str) -> None:
    conn.execute("delete from content_chunks where video_id = %s", (video_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into content_chunks (video_id, channel_id, chunk_index, source, start_seconds, content, embedding)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(video_id, channel_id, i, source, ch["start_seconds"], ch["content"], emb)
             for i, (ch, emb) in enumerate(zip(chunks, embeddings))],
        )
    conn.execute(
        "update videos set transcript_lang = %s, transcript_status = %s where video_id = %s",
        (transcript_lang, transcript_status, video_id),
    )

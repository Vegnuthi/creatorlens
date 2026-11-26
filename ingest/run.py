"""Ingest YouTube channels into CreatorLens.

Examples:
    python -m ingest.run --file seeds/channels.txt
    python -m ingest.run @CodeWithHarry @takeUforward --max-videos 20
    python -m ingest.run --file seeds/channels.txt --stats-only     # daily refresh, no transcripts
    python -m ingest.run --file seeds/more_channels.txt --titles-only   # add creators without captions
    python -m ingest.run --file seeds/more_channels.txt --retry-missing # later: fetch their captions
"""
import argparse
import sys
import time

from . import db, youtube_api
from .chunking import chunk_snippets, metadata_chunk
from .config import CHUNK_WORDS
from .embeddings import embed_passages
from .transcripts import TranscriptResult, YouTubeBlocked, fetch_transcript


def read_channel_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()]


def ingest_channel(conn, identifier: str, max_videos: int, stats_only: bool, pause: float,
                   retry_missing: bool = False, titles_only: bool = False) -> None:
    creator = youtube_api.resolve_channel(identifier)
    if not creator:
        print(f"✗ {identifier}: channel not found, skipping")
        return
    print(f"→ {creator['title']} ({creator['subscriber_count']:,} subs)" if creator["subscriber_count"]
          else f"→ {creator['title']} (hidden subscriber count)")

    db.upsert_creator(conn, creator)
    video_ids = youtube_api.recent_video_ids(creator["uploads_playlist"], max_videos)
    videos = youtube_api.video_details(video_ids)
    db.upsert_videos(conn, videos)
    conn.commit()
    if stats_only:
        print(f"  refreshed stats for {len(videos)} videos")
        return

    todo = db.videos_to_index(conn, [v["video_id"] for v in videos], retry_missing)
    print(f"  {len(videos)} videos, {len(todo)} new to index")
    for v in videos:
        if v["video_id"] not in todo:
            continue
        tr = TranscriptResult([], None, "none") if titles_only else fetch_transcript(v["video_id"])
        if tr.snippets:
            chunks, source = chunk_snippets(tr.snippets, CHUNK_WORDS), "transcript"
        else:
            chunks, source = [metadata_chunk(v["title"], v["description"])], "metadata"
        # prefix every chunk with the video title: gives the model context for short passages
        texts = [f"{v['title']}: {c['content']}" for c in chunks]
        embeddings = embed_passages(texts)
        db.save_chunks(conn, v["video_id"], v["channel_id"], source, chunks, embeddings,
                       tr.language or v.get("default_audio_language"), tr.status)
        conn.commit()
        print(f"    ✓ {v['title'][:60]} — {len(chunks)} chunks ({tr.status})")
        if not titles_only:
            time.sleep(pause)  # be polite to YouTube's caption endpoint


def main() -> None:
    # Windows consoles default to cp1252, which cannot print the ✓/→ symbols below
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Ingest YouTube channels into CreatorLens")
    p.add_argument("channels", nargs="*", help="handles (@name), channel IDs or URLs")
    p.add_argument("--file", help="text file with one channel per line")
    p.add_argument("--max-videos", type=int, default=30)
    p.add_argument("--stats-only", action="store_true", help="refresh numbers only, skip transcripts")
    p.add_argument("--pause", type=float, default=3.0, help="seconds between caption requests")
    p.add_argument("--titles-only", action="store_true",
                   help="index titles + descriptions only (no captions); upgrade later with --retry-missing")
    p.add_argument("--retry-missing", action="store_true",
                   help="re-try videos that were indexed earlier without captions")
    args = p.parse_args()

    channels = list(args.channels) + (read_channel_file(args.file) if args.file else [])
    if not channels:
        p.error("give channels as arguments or with --file")

    with db.connect() as conn:
        for ident in channels:
            try:
                ingest_channel(conn, ident, args.max_videos, args.stats_only, args.pause,
                               args.retry_missing, args.titles_only)
            except YouTubeBlocked:
                conn.rollback()
                print("\n✗ YouTube has temporarily blocked caption downloads from this internet connection.")
                print("  Everything saved so far is kept. Switch networks (e.g. phone hotspot) or wait a few hours,")
                print("  then run again with --retry-missing to continue where this stopped.")
                return
            except Exception as exc:
                conn.rollback()
                print(f"✗ {ident}: {exc}")
    print("done")


if __name__ == "__main__":
    main()

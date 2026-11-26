"""Thin wrapper around the YouTube Data API v3.

Quota cost is low: each call below costs 1 unit, and the free daily quota is 10,000 units.
Ingesting one channel with 30 videos uses roughly 3-4 units.
"""
import re
import requests

from .config import YOUTUBE_API_KEY

BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeError(RuntimeError):
    pass


def _get(endpoint: str, **params) -> dict:
    if not YOUTUBE_API_KEY:
        raise YouTubeError("YOUTUBE_API_KEY is not set (see .env.example)")
    params["key"] = YOUTUBE_API_KEY
    resp = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30)
    if resp.status_code != 200:
        raise YouTubeError(f"{endpoint} failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def resolve_channel(identifier: str) -> dict | None:
    """Accepts a channel ID (UC...), a handle (@name) or a channel URL."""
    identifier = identifier.strip()
    m = re.search(r"(UC[\w-]{22})", identifier)
    if m:
        params = {"id": m.group(1)}
    else:
        handle = identifier.rstrip("/").split("/")[-1]
        if not handle.startswith("@"):
            handle = "@" + handle
        params = {"forHandle": handle}

    data = _get("channels", part="snippet,statistics,contentDetails,brandingSettings", **params)
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    sn, st = item["snippet"], item.get("statistics", {})
    return {
        "channel_id": item["id"],
        "handle": sn.get("customUrl"),
        "title": sn.get("title"),
        "description": sn.get("description"),
        "country": sn.get("country"),
        "default_language": sn.get("defaultLanguage")
            or item.get("brandingSettings", {}).get("channel", {}).get("defaultLanguage"),
        "thumbnail_url": (sn.get("thumbnails", {}).get("medium") or sn.get("thumbnails", {}).get("default") or {}).get("url"),
        "subscriber_count": int(st["subscriberCount"]) if "subscriberCount" in st else None,
        "view_count": int(st.get("viewCount", 0)),
        "video_count": int(st.get("videoCount", 0)),
        "published_at": sn.get("publishedAt"),
        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def recent_video_ids(uploads_playlist: str, max_videos: int = 30) -> list[str]:
    ids, page = [], None
    while len(ids) < max_videos:
        data = _get("playlistItems", part="contentDetails", playlistId=uploads_playlist,
                    maxResults=min(50, max_videos - len(ids)), **({"pageToken": page} if page else {}))
        ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return ids


def parse_duration(iso: str | None) -> int | None:
    """'PT1H2M3S' -> 3723"""
    if not iso:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def video_details(video_ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(video_ids), 50):
        data = _get("videos", part="snippet,statistics,contentDetails", id=",".join(video_ids[i:i + 50]))
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            out.append({
                "video_id": v["id"],
                "channel_id": sn["channelId"],
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "published_at": sn.get("publishedAt"),
                "duration_seconds": parse_duration(v.get("contentDetails", {}).get("duration")),
                "view_count": int(st.get("viewCount", 0)),
                "like_count": int(st["likeCount"]) if "likeCount" in st else None,
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
                "default_audio_language": sn.get("defaultAudioLanguage"),
            })
    return out

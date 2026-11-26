"""Fetch video captions. English captions are used directly; Hindi (or any other language)
captions are machine-translated to English by YouTube when possible; otherwise the original
text is kept and the multilingual embedding model matches it against English briefs.

Note: YouTube often blocks caption requests from cloud servers. Run ingestion from your laptop.
"""
from dataclasses import dataclass

from youtube_transcript_api import (YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled,
                                    IpBlocked, RequestBlocked)

ENGLISH = ["en", "en-IN", "en-US", "en-GB"]
_api = YouTubeTranscriptApi()


class YouTubeBlocked(RuntimeError):
    """YouTube stopped serving captions to this internet connection (rate limit)."""


@dataclass
class TranscriptResult:
    snippets: list[tuple[float, str]]  # (start_seconds, text)
    language: str | None
    status: str                         # 'native_en' | 'translated' | 'original' | 'none'


def fetch_transcript(video_id: str) -> TranscriptResult:
    try:
        available = _api.list(video_id)
    except (TranscriptsDisabled, NoTranscriptFound):
        return TranscriptResult([], None, "none")
    except (IpBlocked, RequestBlocked) as exc:
        raise YouTubeBlocked(str(exc)[:200]) from exc
    except Exception as exc:  # network blocks, age-restricted videos, etc.
        print(f"    ! captions unavailable for {video_id}: {type(exc).__name__}")
        return TranscriptResult([], None, "none")

    try:
        t = available.find_transcript(ENGLISH)
        status = "native_en"
        original_lang = t.language_code
    except NoTranscriptFound:
        first = next(iter(available), None)
        if first is None:
            return TranscriptResult([], None, "none")
        original_lang = first.language_code
        if first.is_translatable:
            t, status = first.translate("en"), "translated"
        else:
            # e.g. Hindi auto-captions: keep the original text, the multilingual model reads it
            t, status = first, "original"

    try:
        fetched = t.fetch()
    except (IpBlocked, RequestBlocked) as exc:
        raise YouTubeBlocked(str(exc)[:200]) from exc
    except Exception as exc:
        print(f"    ! caption fetch failed for {video_id}: {type(exc).__name__}")
        return TranscriptResult([], original_lang, "none")

    snippets = [(s.start, s.text) for s in fetched.snippets if s.text and s.text.strip()]
    return TranscriptResult(snippets, original_lang, status if snippets else "none")

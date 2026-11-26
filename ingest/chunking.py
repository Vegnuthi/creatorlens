"""Split transcripts into ~150-word passages that keep their start time,
so search results can link straight to the moment in the video."""
import re

NOISE = re.compile(r"\[(music|applause|laughter|संगीत)\]|\s+", re.IGNORECASE)
URLS = re.compile(r"https?://\S+")


def clean(text: str) -> str:
    return NOISE.sub(" ", text).strip()


def chunk_snippets(snippets: list[tuple[float, str]], target_words: int = 150) -> list[dict]:
    chunks, words, start = [], [], None
    for ts, text in snippets:
        text = clean(text)
        if not text:
            continue
        if start is None:
            start = ts
        words.extend(text.split())
        if len(words) >= target_words:
            chunks.append({"start_seconds": int(start), "content": " ".join(words)})
            words, start = [], None
    if words:
        # merge a short tail into the previous chunk instead of creating a tiny one
        if chunks and len(words) < target_words // 3:
            chunks[-1]["content"] += " " + " ".join(words)
        else:
            chunks.append({"start_seconds": int(start or 0), "content": " ".join(words)})
    return chunks


def metadata_chunk(title: str, description: str, max_words: int = 120) -> dict:
    """Fallback when a video has no captions: title + first part of the description."""
    desc = URLS.sub("", description or "")
    desc_words = clean(desc).split()[:max_words]
    return {"start_seconds": 0, "content": f"{title}. {' '.join(desc_words)}".strip()}

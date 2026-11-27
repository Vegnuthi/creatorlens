"""CreatorLens API: semantic creator search + outreach shortlists."""
import csv
import io
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
import requests
from pydantic import BaseModel, Field, field_validator

from ingest.config import DATABASE_URL
from ingest.embeddings import embed_query

STATIC = Path(__file__).parent / "static"
# "0" on small servers: the browser computes the query embedding and sends it with the search
SERVER_EMBEDDINGS = os.getenv("SERVER_EMBEDDINGS", "1") == "1"
Status = Literal["to_contact", "contacted", "replied", "negotiating", "signed", "rejected"]


def configure_connection(conn) -> None:
    from pgvector.psycopg import register_vector
    register_vector(conn)


pool: ConnectionPool | None = None


def _masked(text: str) -> str:
    return re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", r"\1****@", text)


def check_database() -> None:
    """Log the real connection problem at startup (the pool alone only reports a timeout)."""
    url = DATABASE_URL.strip()
    if not url.startswith(("postgresql://", "postgres://")):
        print(f"DATABASE_URL looks wrong: it should start with postgresql:// but starts with {url[:15]!r}")
        return
    host = url.split("@")[-1].split("/")[0] if "@" in url else "?"
    try:
        psycopg.connect(url, connect_timeout=10).close()
        print(f"database: connected to {host}")
    except Exception as exc:
        print(f"database: CONNECTION FAILED to {host}: {type(exc).__name__}: {_masked(str(exc))[:300]}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global pool
    check_database()
    pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, configure=configure_connection,
                          kwargs={"row_factory": dict_row, "prepare_threshold": None}, open=True)
    # Load the embedding model once, before the server accepts traffic
    if SERVER_EMBEDDINGS and os.getenv("WARM_MODEL", "1") == "1":
        try:
            embed_query("warm up")
        except Exception as exc:  # never block startup; the first search will retry
            print(f"warm-up skipped: {exc}")
    yield
    pool.close()


app = FastAPI(title="CreatorLens", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def query(sql: str, params=None, one: bool = False):
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        if cur.description is None:
            return None
        return cur.fetchone() if one else cur.fetchall()


# ---------- pages ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/privacy", include_in_schema=False)
def privacy():
    return FileResponse(STATIC / "privacy.html")


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- search ----------

class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    min_subs: int = Field(0, ge=0)
    max_subs: int | None = Field(None, ge=0)
    language: str | None = None
    recent_days: int = Field(730, ge=30, le=3650)
    limit: int = Field(20, ge=1, le=50)
    embedding: list[float] | None = None   # 384 numbers computed in the browser (optional)

    @field_validator("embedding")
    @classmethod
    def check_dim(cls, v):
        if v is not None and len(v) != 384:
            raise ValueError("embedding must have 384 numbers")
        return v


@app.post("/api/search")
def search(req: SearchRequest):
    if req.embedding is not None:
        vec = np.asarray(req.embedding, dtype=np.float32)
    elif SERVER_EMBEDDINGS:
        vec = embed_query(req.query)
    else:
        raise HTTPException(400, "Your browser couldn't load the search model. Refresh the page and try again.")
    rows = query(
        "select * from match_creators(%s, %s, %s, %s, %s, %s)",
        (vec, req.min_subs, req.max_subs, req.language or None, req.recent_days, req.limit),
    )
    query(
        "insert into search_logs (query, filters, result_count, top_channel) values (%s, %s, %s, %s)",
        (req.query, Jsonb(req.model_dump(exclude={"query", "embedding"})), len(rows), rows[0]["channel_id"] if rows else None),
    )
    return {"query": req.query, "results": rows}


# ---------- creators ----------

@app.get("/api/creators/{channel_id}")
def creator_detail(channel_id: str):
    creator = query(
        """
        select c.*, g.subs_gained, g.growth_pct, k.uploads_180d, k.avg_days_between_uploads
        from creators c
        left join creator_growth_30d g using (channel_id)
        left join creator_upload_cadence k using (channel_id)
        where c.channel_id = %s
        """,
        (channel_id,), one=True,
    )
    if not creator:
        raise HTTPException(404, "creator not found")
    creator["recent_videos"] = query(
        """
        select video_id, title, published_at, view_count, like_count, comment_count, is_short, transcript_status
        from videos where channel_id = %s order by published_at desc limit 8
        """,
        (channel_id,),
    )
    creator["snapshots"] = query(
        "select snapshot_date, subscriber_count from creator_snapshots where channel_id = %s order by snapshot_date",
        (channel_id,),
    )
    return creator


# ---------- shortlists (outreach tracker) ----------
# Each browser sends a random visitor ID (X-Visitor-Id); its shortlists are private to it.
# Shortlists owned by "demo" are examples everyone can view; only the site owner
# (X-Owner-Key matching the OWNER_KEY setting) can edit them.

DEMO = "demo"
OWNER_KEY = os.getenv("OWNER_KEY", "").strip()
OWNER_EMAILS = {e.strip().lower() for e in os.getenv("OWNER_EMAILS", "").split(",") if e.strip()}
VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Google sign-in runs through Supabase Auth. These two values are public by design.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
_token_cache: dict[str, tuple[float, dict | None]] = {}


def signed_in_user(request: Request) -> dict | None:
    """Return {'id', 'email'} for a valid Supabase login token, else None."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or not SUPABASE_URL:
        return None
    token = auth[7:].strip()
    now = time.time()
    cached = _token_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]
    user = None
    try:
        resp = requests.get(f"{SUPABASE_URL}/auth/v1/user", timeout=10,
                            headers={"apikey": SUPABASE_PUBLISHABLE_KEY, "Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            data = resp.json()
            user = {"id": data["id"], "email": (data.get("email") or "").lower()}
    except requests.RequestException:
        raise HTTPException(503, "Couldn't check your login right now. Try again in a moment.")
    if len(_token_cache) > 1000:
        _token_cache.clear()
    _token_cache[token] = (now + 300, user)
    if user is None:
        raise HTTPException(401, "Your login has expired. Sign in again.")
    return user


def current_owner(request: Request, visitor_param: str | None = None) -> str:
    key = request.headers.get("x-owner-key", "")
    if OWNER_KEY and key and secrets.compare_digest(key, OWNER_KEY):
        return DEMO
    user = signed_in_user(request)
    if user:
        return DEMO if user["email"] in OWNER_EMAILS else f"user:{user['id']}"
    visitor = request.headers.get("x-visitor-id") or visitor_param or ""
    if not VISITOR_RE.match(visitor) or visitor == DEMO:
        raise HTTPException(400, "Missing visitor ID. Refresh the page and try again.")
    return visitor


def readable_shortlist(shortlist_id: int, owner: str) -> dict:
    sl = query("select * from shortlists where id = %s and owner_id in (%s, %s)",
               (shortlist_id, owner, DEMO), one=True)
    if not sl:
        raise HTTPException(404, "shortlist not found")
    return sl


def editable_shortlist(shortlist_id: int, owner: str) -> dict:
    sl = readable_shortlist(shortlist_id, owner)
    if sl["owner_id"] != owner:
        raise HTTPException(403, "Example shortlists are read-only. Create your own shortlist to save creators.")
    return sl


class ShortlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    brief: str | None = Field(None, max_length=500)


class AddCreatorIn(BaseModel):
    channel_id: str
    match_score: float | None = None


class UpdateCreatorIn(BaseModel):
    status: Status | None = None
    notes: str | None = Field(None, max_length=1000)


@app.get("/api/config")
def config():
    """Public settings the page needs to show the Google sign-in button."""
    if not (SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY):
        return {"login": False}
    return {"login": True, "supabase_url": SUPABASE_URL, "supabase_key": SUPABASE_PUBLISHABLE_KEY}


@app.get("/api/me")
def me(request: Request):
    user = signed_in_user(request)
    if not user:
        return {"signed_in": False}
    return {"signed_in": True, "email": user["email"], "is_owner": user["email"] in OWNER_EMAILS}


@app.post("/api/shortlists/claim")
def claim_guest_shortlists(request: Request):
    """After sign-in, move the shortlists this browser made as a guest into the account."""
    user = signed_in_user(request)
    visitor = request.headers.get("x-visitor-id", "")
    if not user or not VISITOR_RE.match(visitor) or visitor == DEMO:
        raise HTTPException(400, "Sign in first.")
    owner = DEMO if user["email"] in OWNER_EMAILS else f"user:{user['id']}"
    rows = query("update shortlists set owner_id = %s where owner_id = %s returning id", (owner, visitor))
    return {"moved": len(rows)}


@app.get("/api/shortlists")
def list_shortlists(request: Request):
    owner = current_owner(request)
    return query(
        """
        select s.id, s.name, s.brief, s.created_at, f.to_contact, f.contacted, f.replied,
               f.negotiating, f.signed, f.rejected, f.reply_rate_pct,
               (select count(*) from shortlist_creators sc where sc.shortlist_id = s.id) as total,
               s.owner_id = %(owner)s as editable,
               s.owner_id = 'demo'     as is_example
        from shortlists s join shortlist_funnel f on f.shortlist_id = s.id
        where s.owner_id in (%(owner)s, 'demo')
        order by (s.owner_id = %(owner)s) desc, s.created_at desc
        """,
        {"owner": owner},
    )


@app.post("/api/shortlists", status_code=201)
def create_shortlist(body: ShortlistIn, request: Request):
    owner = current_owner(request)
    if owner != DEMO:
        count = query("select count(*) as n from shortlists where owner_id = %s", (owner,), one=True)["n"]
        if count >= 20:
            raise HTTPException(429, "You've reached 20 shortlists. Remove one before creating another.")
    return query("insert into shortlists (name, brief, owner_id) values (%s, %s, %s) returning *",
                 (body.name, body.brief, owner), one=True)


@app.get("/api/shortlists/{shortlist_id}")
def get_shortlist(shortlist_id: int, request: Request):
    owner = current_owner(request)
    sl = readable_shortlist(shortlist_id, owner)
    sl["editable"] = sl["owner_id"] == owner
    sl.pop("owner_id", None)
    sl["creators"] = query(
        """
        select sc.channel_id, sc.status, sc.match_score, sc.notes, sc.updated_at,
               c.title, c.handle, c.thumbnail_url, c.subscriber_count
        from shortlist_creators sc join creators c using (channel_id)
        where sc.shortlist_id = %s order by sc.match_score desc nulls last
        """,
        (shortlist_id,),
    )
    return sl


@app.post("/api/shortlists/{shortlist_id}/creators", status_code=201)
def add_to_shortlist(shortlist_id: int, body: AddCreatorIn, request: Request):
    editable_shortlist(shortlist_id, current_owner(request))
    return query(
        """
        insert into shortlist_creators (shortlist_id, channel_id, match_score) values (%s, %s, %s)
        on conflict (shortlist_id, channel_id) do update set match_score = excluded.match_score, updated_at = now()
        returning *
        """,
        (shortlist_id, body.channel_id, body.match_score), one=True,
    )


@app.patch("/api/shortlists/{shortlist_id}/creators/{channel_id}")
def update_shortlist_creator(shortlist_id: int, channel_id: str, body: UpdateCreatorIn, request: Request):
    editable_shortlist(shortlist_id, current_owner(request))
    row = query(
        """
        update shortlist_creators
        set status = coalesce(%s, status), notes = coalesce(%s, notes), updated_at = now()
        where shortlist_id = %s and channel_id = %s returning *
        """,
        (body.status, body.notes, shortlist_id, channel_id), one=True,
    )
    if not row:
        raise HTTPException(404, "creator not in shortlist")
    return row


@app.delete("/api/shortlists/{shortlist_id}/creators/{channel_id}", status_code=204)
def remove_from_shortlist(shortlist_id: int, channel_id: str, request: Request):
    editable_shortlist(shortlist_id, current_owner(request))
    query("delete from shortlist_creators where shortlist_id = %s and channel_id = %s", (shortlist_id, channel_id))


@app.delete("/api/shortlists/{shortlist_id}", status_code=204)
def delete_shortlist(shortlist_id: int, request: Request):
    editable_shortlist(shortlist_id, current_owner(request))
    query("delete from shortlists where id = %s", (shortlist_id,))


@app.get("/api/shortlists/{shortlist_id}/export.csv")
def export_shortlist(shortlist_id: int, request: Request, v: str | None = None):
    # a download link can't send headers, so the visitor ID comes as ?v=
    readable_shortlist(shortlist_id, current_owner(request, v))
    rows = query(
        """
        select c.title as creator, 'https://youtube.com/channel/' || c.channel_id as channel_url,
               c.subscriber_count, sc.match_score, sc.status, sc.notes
        from shortlist_creators sc join creators c using (channel_id)
        where sc.shortlist_id = %s order by sc.match_score desc nulls last
        """,
        (shortlist_id,),
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["creator", "channel_url", "subscriber_count", "match_score", "status", "notes"])
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=shortlist-{shortlist_id}.csv"})


# ---------- insights ----------

@app.get("/api/insights")
def insights():
    totals = query(
        """
        select (select count(*) from creators) as creators,
               (select count(*) from videos) as videos,
               (select count(*) from content_chunks) as chunks,
               (select count(*) from videos where transcript_status = 'translated') as translated_videos,
               (select count(*) from search_logs) as searches
        """,
        one=True,
    )
    trends = query("select * from search_trends limit 10")
    return {"totals": totals, "top_searches": trends}

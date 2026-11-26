"""CreatorLens API: semantic creator search + outreach shortlists."""
import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from ingest.config import DATABASE_URL
from ingest.embeddings import embed_query

STATIC = Path(__file__).parent / "static"
Status = Literal["to_contact", "contacted", "replied", "negotiating", "signed", "rejected"]


def configure_connection(conn) -> None:
    from pgvector.psycopg import register_vector
    register_vector(conn)


pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global pool
    pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, configure=configure_connection,
                          kwargs={"row_factory": dict_row, "prepare_threshold": None}, open=True)
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


@app.post("/api/search")
def search(req: SearchRequest):
    vec = embed_query(req.query)
    rows = query(
        "select * from match_creators(%s, %s, %s, %s, %s, %s)",
        (vec, req.min_subs, req.max_subs, req.language or None, req.recent_days, req.limit),
    )
    query(
        "insert into search_logs (query, filters, result_count, top_channel) values (%s, %s, %s, %s)",
        (req.query, Jsonb(req.model_dump(exclude={"query"})), len(rows), rows[0]["channel_id"] if rows else None),
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

class ShortlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    brief: str | None = None


class AddCreatorIn(BaseModel):
    channel_id: str
    match_score: float | None = None


class UpdateCreatorIn(BaseModel):
    status: Status | None = None
    notes: str | None = None


@app.get("/api/shortlists")
def list_shortlists():
    return query(
        """
        select s.id, s.name, s.brief, s.created_at, f.to_contact, f.contacted, f.replied,
               f.negotiating, f.signed, f.rejected, f.reply_rate_pct,
               (select count(*) from shortlist_creators sc where sc.shortlist_id = s.id) as total
        from shortlists s join shortlist_funnel f on f.shortlist_id = s.id
        order by s.created_at desc
        """
    )


@app.post("/api/shortlists", status_code=201)
def create_shortlist(body: ShortlistIn):
    return query("insert into shortlists (name, brief) values (%s, %s) returning *",
                 (body.name, body.brief), one=True)


@app.get("/api/shortlists/{shortlist_id}")
def get_shortlist(shortlist_id: int):
    sl = query("select * from shortlists where id = %s", (shortlist_id,), one=True)
    if not sl:
        raise HTTPException(404, "shortlist not found")
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
def add_to_shortlist(shortlist_id: int, body: AddCreatorIn):
    row = query(
        """
        insert into shortlist_creators (shortlist_id, channel_id, match_score) values (%s, %s, %s)
        on conflict (shortlist_id, channel_id) do update set match_score = excluded.match_score, updated_at = now()
        returning *
        """,
        (shortlist_id, body.channel_id, body.match_score), one=True,
    )
    return row


@app.patch("/api/shortlists/{shortlist_id}/creators/{channel_id}")
def update_shortlist_creator(shortlist_id: int, channel_id: str, body: UpdateCreatorIn):
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
def remove_from_shortlist(shortlist_id: int, channel_id: str):
    query("delete from shortlist_creators where shortlist_id = %s and channel_id = %s", (shortlist_id, channel_id))


@app.get("/api/shortlists/{shortlist_id}/export.csv")
def export_shortlist(shortlist_id: int):
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

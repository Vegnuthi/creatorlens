# CreatorLens

**Find YouTube creators by what they actually say on camera.**

**Live demo:** https://creatorlens-09ye.onrender.com (free server: the first visit can take ~50 seconds to wake up)

Brands and ed-tech teams usually choose creators by niche label and subscriber count. A "tech" channel with 1M subscribers might never talk about the topic your campaign needs, and its audience might have stopped watching. CreatorLens reads creators' video transcripts, matches them against your campaign brief by meaning, and ranks creators by **topic fit, engagement and real reach**. Every result shows the exact quotes behind the match, with a link to that moment in the video.

It also includes a small outreach tracker: shortlist creators for a campaign, move them through *to contact → contacted → replied → signed*, and export the list as CSV.

## How it works

```
YouTube Data API ──┐
                   ├─► Python ingestion ─► chunk transcripts (~150 words, with timestamps)
YouTube captions ──┘    (Hindi kept as-is)
                                │
                                ▼
                     fastembed (paraphrase-multilingual-MiniLM-L12-v2, 384-dim)
                                │
                                ▼
            PostgreSQL + pgvector (Supabase) ◄── FastAPI ◄── search page (query embedded in-browser)
              match_creators() ranking function
```

1. **Ingestion (Python):** resolves each channel, pulls its recent videos and their stats, downloads captions, and splits them into passages that keep their start time. A multilingual model reads Hindi, Hinglish and English captions, so an English brief also finds Hindi-speaking creators. Videos without captions fall back to title + description.
2. **Embeddings:** every passage becomes a 384-number vector that represents its meaning.
3. **Ranking (SQL):** `match_creators()` finds the passages closest to the brief, rolls them up per creator, and blends:
   - **Topic fit (75%)**: similarity of the creator's 3 best passages (scaled within each search), plus how many different videos strongly match
   - **Engagement (15%)**: (likes + comments) / views over the last 6 months, ranked against the other results
   - **Reach (10%)**: median views ÷ subscribers, which catches channels whose subscribers no longer watch
4. **App (FastAPI + vanilla JS):** search, creator details, shortlists, CSV export. The query embedding is computed client-side with transformers.js, so hosting stays free.

## SQL highlights

The database does the analysis, not just storage (`db/03_views.sql`, `db/04_functions.sql`):

| Object | Technique |
|---|---|
| `match_creators()` | pgvector cosine search, CTE pipeline, `row_number()` per creator and per video, `cume_dist()` percentile scoring, `percentile_cont()` median, `jsonb_agg` evidence |
| `creator_growth_30d` | `first_value` / `last_value` window over daily snapshots |
| `creator_upload_cadence` | `lag()` to compute days between uploads |
| `shortlist_funnel` | `count(*) filter (...)` pivot of outreach stages + reply rate |
| `search_trends` | `date_trunc` weekly aggregation of what brands search for |

## Setup (about 30 minutes)

### 1. Database: Supabase (free)
1. Create a project at [supabase.com](https://supabase.com) (pick the Mumbai region).
2. Open **SQL Editor** and run, in order: `db/01_schema.sql`, `db/02_indexes.sql`, `db/03_views.sql`, `db/04_functions.sql`, `db/05_security.sql`.
3. Copy the connection string from **Connect → Session pooler**.

### 2. YouTube API key (free)
In [Google Cloud Console](https://console.cloud.google.com/): create a project → enable **YouTube Data API v3** → **Credentials → Create API key**. The free quota (10,000 units/day) covers thousands of channels; one channel costs about 4 units.

### 3. Run locally
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in both values

# Load creators (run this on your laptop: YouTube blocks caption downloads from most cloud servers)
python -m ingest.run --file seeds/channels.txt --max-videos 30

# Start the site
uvicorn app.main:app --reload
# open http://localhost:8000
```
The first run downloads the embedding model (~220 MB) once. YouTube rate-limits caption downloads (roughly 40–50 per connection before a temporary block). Load in small batches with `--max-videos 12 --pause 6`; if blocked, the script stops cleanly, and `--retry-missing` continues later. `--titles-only` adds creators immediately and captions can be filled in afterwards.

### 4. Deploy
- **Web app → [Render](https://render.com) (free):** push this repo to GitHub, create a *Blueprint* from `render.yaml`, and set `DATABASE_URL`. The free server has 512 MB of memory, which is too small for the embedding model, so the search page runs the same model **in the visitor's browser** with [transformers.js](https://huggingface.co/docs/transformers.js) (a ~120 MB download on the first search, cached afterwards) and sends the 384-number query vector to the API. The server only runs SQL. Ingestion still runs the model locally on your laptop.
- **Daily stats refresh → GitHub Actions:** add `YOUTUBE_API_KEY` and `DATABASE_URL` as repository secrets. `.github/workflows/refresh-stats.yml` then records subscriber counts every morning, which powers the 30-day growth numbers.
- To add new creators later, run the ingest command from your laptop again; it skips videos already indexed.

## Tests
```bash
bash tests/test_sql.sh          # schema, views and ranking function on plain Postgres
python tests/test_api.py        # every API endpoint against the same test database
```
The tests use `tests/vector_shim.sql`, a small stand-in for pgvector, so they run on any Postgres without extensions.

## Useful queries for your write-up
```sql
-- Which creators keep showing up across searches?
select c.title, count(*) as times_top_result
from search_logs s join creators c on c.channel_id = s.top_channel
group by c.title order by times_top_result desc;

-- How much of the index came from Hindi videos via translation?
select transcript_status, count(*) from videos group by 1;

-- Creators with big subscriber counts but weak current reach
select title, subscriber_count,
       round(avg(v.view_count) / subscriber_count * 100, 1) as avg_views_pct_of_subs
from creators c join videos v using (channel_id)
where v.published_at > now() - interval '90 days'
group by c.channel_id having count(*) >= 3
order by avg_views_pct_of_subs;
```

## Ideas for v2
- Instagram Reels and podcasts as additional sources
- Comment analysis: what the audience asks about, not just what the creator says
- Brand-safety flags (passages mentioning competitors, controversial topics)
- Estimated cost per video from reach and engagement


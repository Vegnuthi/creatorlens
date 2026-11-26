-- CreatorLens schema
-- Run in order: 01_schema.sql -> 02_indexes.sql -> 03_views.sql -> 04_functions.sql
-- Embeddings: 384 dimensions (BAAI/bge-small-en-v1.5). Change everywhere if you switch models.

create extension if not exists vector;

-- One row per YouTube channel
create table if not exists creators (
    channel_id        text primary key,
    handle            text,
    title             text not null,
    description       text,
    country           text,
    default_language  text,
    thumbnail_url     text,
    subscriber_count  bigint,
    view_count        bigint,
    video_count       integer,
    published_at      timestamptz,
    last_synced_at    timestamptz default now()
);

-- Daily stats history, so growth can be computed with window functions
create table if not exists creator_snapshots (
    channel_id        text references creators(channel_id) on delete cascade,
    snapshot_date     date not null default current_date,
    subscriber_count  bigint,
    view_count        bigint,
    video_count       integer,
    primary key (channel_id, snapshot_date)
);

create table if not exists videos (
    video_id          text primary key,
    channel_id        text not null references creators(channel_id) on delete cascade,
    title             text not null,
    description       text,
    published_at      timestamptz,
    duration_seconds  integer,
    view_count        bigint,
    like_count        bigint,
    comment_count     bigint,
    transcript_lang   text,          -- original caption language, e.g. 'hi'
    transcript_status text,          -- 'native_en' | 'translated' | 'none'
    is_short          boolean generated always as (duration_seconds is not null and duration_seconds <= 60) stored
);

-- Searchable pieces of content. Each chunk ~150 words of transcript,
-- or the title + description when no transcript exists.
create table if not exists content_chunks (
    id             bigserial primary key,
    video_id       text not null references videos(video_id) on delete cascade,
    channel_id     text not null references creators(channel_id) on delete cascade,
    chunk_index    integer not null,
    source         text not null check (source in ('transcript', 'metadata')),
    start_seconds  integer default 0,
    content        text not null,
    embedding      vector(384) not null,
    unique (video_id, chunk_index)
);

-- Outreach workflow: brand campaigns and the creators shortlisted for them
create table if not exists shortlists (
    id          bigserial primary key,
    name        text not null,
    brief       text,
    created_at  timestamptz default now()
);

create table if not exists shortlist_creators (
    shortlist_id  bigint references shortlists(id) on delete cascade,
    channel_id    text references creators(channel_id) on delete cascade,
    status        text not null default 'to_contact'
                  check (status in ('to_contact', 'contacted', 'replied', 'negotiating', 'signed', 'rejected')),
    match_score   numeric(5, 2),
    notes         text,
    added_at      timestamptz default now(),
    updated_at    timestamptz default now(),
    primary key (shortlist_id, channel_id)
);

-- Every search is logged: useful for "what are brands looking for" analytics
create table if not exists search_logs (
    id            bigserial primary key,
    query         text not null,
    filters       jsonb,
    result_count  integer,
    top_channel   text,
    created_at    timestamptz default now()
);

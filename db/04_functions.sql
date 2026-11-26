-- ============================================================
-- match_creators: the core ranking query
-- 1. Finds the content chunks closest in meaning to the brief
-- 2. Rolls chunk matches up to the creator level
-- 3. Blends relevance with audience quality (engagement + reach)
-- 4. Returns the best 3 videos per creator as "evidence"
-- ============================================================
create or replace function match_creators(
    query_embedding  vector(384),
    min_subs         bigint  default 0,
    max_subs         bigint  default null,
    lang_filter      text    default null,
    recent_days      integer default 730,
    match_count      integer default 20,
    candidate_pool   integer default 500
)
returns table (
    channel_id        text,
    title             text,
    handle            text,
    thumbnail_url     text,
    country           text,
    subscriber_count  bigint,
    match_score       numeric,
    relevance_score   numeric,
    engagement_rate   numeric,
    reach_ratio       numeric,
    median_views      numeric,
    matching_videos   bigint,
    recent_videos     bigint,
    last_upload       timestamptz,
    growth_30d_pct    numeric,
    evidence          jsonb
)
language sql stable
as $$
with candidates as (
    select ch.channel_id, ch.video_id, ch.content, ch.start_seconds,
           1 - (ch.embedding <=> query_embedding) as similarity
    from content_chunks ch
    join creators cr on cr.channel_id = ch.channel_id
    join videos  v  on v.video_id   = ch.video_id
    where cr.subscriber_count >= min_subs
      and (max_subs is null or cr.subscriber_count <= max_subs)
      and (lang_filter is null
           or cr.default_language ilike lang_filter || '%'
           or v.transcript_lang  ilike lang_filter || '%')
      and v.published_at >= now() - make_interval(days => recent_days)
    order by ch.embedding <=> query_embedding
    limit candidate_pool
),
ranked as (
    select c.*,
           row_number() over (partition by c.channel_id order by c.similarity desc)              as rank_in_channel,
           row_number() over (partition by c.channel_id, c.video_id order by c.similarity desc) as rank_in_video
    from candidates c
),
relevance as (
    select r.channel_id,
           avg(r.similarity) filter (where r.rank_in_channel <= 3) as top3_similarity,
           count(distinct r.video_id)                              as matching_videos
    from ranked r
    group by r.channel_id
),
evidence as (
    -- best chunk of each video, top 3 videos per creator
    select e.channel_id,
           jsonb_agg(jsonb_build_object(
               'video_id',      e.video_id,
               'video_title',   e.video_title,
               'snippet',       left(e.content, 260),
               'start_seconds', e.start_seconds,
               'similarity',    round(e.similarity::numeric, 3)
           ) order by e.similarity desc) as evidence
    from (
        select r.*, v.title as video_title,
               row_number() over (partition by r.channel_id order by r.similarity desc) as video_rank
        from ranked r
        join videos v on v.video_id = r.video_id
        where r.rank_in_video = 1
    ) e
    where e.video_rank <= 3
    group by e.channel_id
),
recent_stats as (
    select v.channel_id,
           count(*)                                                       as recent_videos,
           percentile_cont(0.5) within group (order by v.view_count)     as median_views,
           sum(coalesce(v.like_count, 0) + coalesce(v.comment_count, 0))::numeric
               / nullif(sum(v.view_count), 0)                             as engagement_rate,
           max(v.published_at)                                            as last_upload
    from videos v
    where v.channel_id in (select rl.channel_id from relevance rl)
      and v.published_at >= now() - interval '180 days'
    group by v.channel_id
),
scored as (
    select cr.channel_id, cr.title, cr.handle, cr.thumbnail_url, cr.country, cr.subscriber_count,
           rl.top3_similarity, rl.matching_videos,
           rs.recent_videos, rs.median_views, rs.engagement_rate, rs.last_upload,
           least(1.0, rs.median_views / nullif(cr.subscriber_count, 0)) as reach_ratio,
           -- relevance: closeness to the best match in this result set + breadth across videos
           0.75 * rl.top3_similarity / nullif(max(rl.top3_similarity) over (), 0)
             + 0.25 * least(rl.matching_videos, 5) / 5.0                         as relevance_score,
           cume_dist() over (order by coalesce(rs.engagement_rate, 0))           as engagement_pct,
           cume_dist() over (order by coalesce(rs.median_views / nullif(cr.subscriber_count, 0), 0)) as reach_pct
    from relevance rl
    join creators cr on cr.channel_id = rl.channel_id
    left join recent_stats rs on rs.channel_id = rl.channel_id
)
select s.channel_id, s.title, s.handle, s.thumbnail_url, s.country, s.subscriber_count,
       round((100 * (0.65 * s.relevance_score + 0.20 * s.engagement_pct + 0.15 * s.reach_pct))::numeric, 1) as match_score,
       round((100 * s.relevance_score)::numeric, 1)            as relevance_score,
       round((100 * coalesce(s.engagement_rate, 0))::numeric, 2) as engagement_rate,
       round(coalesce(s.reach_ratio, 0)::numeric, 3)           as reach_ratio,
       round(coalesce(s.median_views, 0)::numeric)             as median_views,
       s.matching_videos,
       coalesce(s.recent_videos, 0)                            as recent_videos,
       s.last_upload,
       g.growth_pct                                            as growth_30d_pct,
       e.evidence
from scored s
left join evidence e on e.channel_id = s.channel_id
left join creator_growth_30d g on g.channel_id = s.channel_id
order by match_score desc
limit match_count;
$$;

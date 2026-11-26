-- ============================================================
-- Analytics views
-- ============================================================

-- Subscriber growth over the last 30 days of snapshots
create or replace view creator_growth_30d as
with window_snapshots as (
    select channel_id, snapshot_date, subscriber_count,
           first_value(subscriber_count) over w as first_subs,
           last_value(subscriber_count)  over w as last_subs,
           count(*) over w                       as snapshots
    from creator_snapshots
    where snapshot_date >= current_date - 30
    window w as (partition by channel_id order by snapshot_date
                 rows between unbounded preceding and unbounded following)
)
select distinct channel_id,
       first_subs,
       last_subs,
       last_subs - first_subs                                               as subs_gained,
       round(100.0 * (last_subs - first_subs) / nullif(first_subs, 0), 2)   as growth_pct
from window_snapshots
where snapshots >= 2;

-- Upload consistency: average gap between uploads (days) per creator, last 180 days
create or replace view creator_upload_cadence as
select channel_id,
       count(*)                                     as uploads_180d,
       round(avg(gap_days)::numeric, 1)             as avg_days_between_uploads
from (
    select channel_id,
           extract(epoch from published_at - lag(published_at) over (partition by channel_id order by published_at)) / 86400 as gap_days
    from videos
    where published_at >= now() - interval '180 days'
) gaps
group by channel_id;

-- Outreach funnel per shortlist (how many creators at each stage)
create or replace view shortlist_funnel as
select s.id as shortlist_id, s.name,
       count(*) filter (where sc.status = 'to_contact')  as to_contact,
       count(*) filter (where sc.status = 'contacted')   as contacted,
       count(*) filter (where sc.status = 'replied')     as replied,
       count(*) filter (where sc.status = 'negotiating') as negotiating,
       count(*) filter (where sc.status = 'signed')      as signed,
       count(*) filter (where sc.status = 'rejected')    as rejected,
       round(100.0 * count(*) filter (where sc.status in ('replied', 'negotiating', 'signed'))
             / nullif(count(*) filter (where sc.status <> 'to_contact'), 0), 1) as reply_rate_pct
from shortlists s
left join shortlist_creators sc on sc.shortlist_id = s.id
group by s.id, s.name;

-- What brands are searching for, week by week
create or replace view search_trends as
select date_trunc('week', created_at)::date as week,
       lower(trim(query))                   as query,
       count(*)                             as searches,
       round(avg(result_count), 1)          as avg_results
from search_logs
group by 1, 2
order by week desc, searches desc;

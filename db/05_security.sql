-- Lock down Supabase's public API. The website connects directly as the database owner
-- (DATABASE_URL), which is not affected by these rules, so the app keeps working.
alter table creators           enable row level security;
alter table creator_snapshots  enable row level security;
alter table videos             enable row level security;
alter table content_chunks     enable row level security;
alter table shortlists         enable row level security;
alter table shortlist_creators enable row level security;
alter table search_logs        enable row level security;

alter view creator_growth_30d     set (security_invoker = true);
alter view creator_upload_cadence set (security_invoker = true);
alter view shortlist_funnel       set (security_invoker = true);
alter view search_trends          set (security_invoker = true);

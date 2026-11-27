-- Private shortlists per visitor.
-- Every browser gets a random visitor ID; its shortlists are visible only to that browser.
-- Rows owned by 'demo' are example shortlists: everyone can view them, only the site owner can edit.
-- Existing shortlists become examples.
alter table shortlists add column if not exists owner_id text not null default 'demo';
create index if not exists shortlists_owner_idx on shortlists (owner_id, created_at desc);

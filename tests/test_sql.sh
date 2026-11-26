#!/usr/bin/env bash
# Tests the schema, views and ranking function on plain Postgres using tests/vector_shim.sql.
# Usage: PSQL="psql -h localhost -U postgres" bash tests/test_sql.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PSQL=${PSQL:-"psql -h /tmp/pgtest -p 5433 -U postgres"}
DB=creatorlens_test

$PSQL -q -c "drop database if exists $DB" -c "create database $DB encoding 'UTF8' template template0"
run() { $PSQL -d $DB -q -v ON_ERROR_STOP=1 "$@"; }

run -f tests/vector_shim.sql
sed -e '/create extension/d' -e 's/vector(384)/vector/g' db/01_schema.sql | run
run -f db/03_views.sql
sed -e 's/vector(384)/vector/g' db/04_functions.sql | run

run <<'SQL'
insert into creators (channel_id, handle, title, default_language, country, subscriber_count) values
 ('UC_A', '@placementguru', 'Placement Guru', 'hi', 'IN', 400000),
 ('UC_B', '@cookwithme',    'Cook With Me',   'en', 'IN', 900000),
 ('UC_C', '@dsawithdev',    'DSA With Dev',   'en', 'IN',  80000);

insert into creator_snapshots values
 ('UC_A', current_date - 20, 380000, 1, 1), ('UC_A', current_date, 400000, 1, 1),
 ('UC_C', current_date - 10,  79000, 1, 1), ('UC_C', current_date,  80000, 1, 1);

insert into videos (video_id, channel_id, title, published_at, duration_seconds, view_count, like_count, comment_count, transcript_lang) values
 ('vA1','UC_A','How I cracked campus placements', now()-interval '10 days', 900, 120000, 9000, 800, 'hi'),
 ('vA2','UC_A','Resume tips for freshers',        now()-interval '40 days', 700,  90000, 6000, 500, 'hi'),
 ('vB1','UC_B','Paneer butter masala',             now()-interval '5 days',  600, 700000, 20000, 900, 'en'),
 ('vC1','UC_C','Top 50 DSA questions for interviews', now()-interval '3 days', 1200, 40000, 4000, 600, 'en'),
 ('vC2','UC_C','Old video',                        now()-interval '1000 days', 1200, 5000, 100, 10, 'en');

insert into content_chunks (video_id, channel_id, chunk_index, source, start_seconds, content, embedding) values
 ('vA1','UC_A',0,'transcript',  0,'placement prep for college students', '{0.9,0.1,0,0.1}'),
 ('vA1','UC_A',1,'transcript',120,'interview questions and offers',       '{0.8,0.3,0,0}'),
 ('vA2','UC_A',0,'transcript',  0,'resume for freshers',                  '{0.7,0.2,0.1,0}'),
 ('vB1','UC_B',0,'transcript',  0,'cooking paneer recipe',                '{0,0,1,0}'),
 ('vC1','UC_C',0,'transcript', 30,'dsa interview preparation',            '{0.85,0.4,0,0}'),
 ('vC2','UC_C',0,'transcript',  0,'very old placement talk',              '{0.95,0.1,0,0}');
SQL

echo "--- all creators, query = 'placement prep' ---"
run -c "select title, match_score, relevance_score, engagement_rate, matching_videos, growth_30d_pct, jsonb_array_length(evidence) as ev from match_creators('{1,0.2,0,0}')"

echo "--- filters: max 100k subs, english ---"
run -c "select title, match_score from match_creators('{1,0.2,0,0}', 0, 100000, 'en')"

echo "--- evidence for top creator ---"
run -c "select jsonb_pretty(evidence) from match_creators('{1,0.2,0,0}', match_count => 1)"

echo "--- views ---"
run -c "insert into shortlists (name) values ('NSAT launch')" \
    -c "insert into shortlist_creators (shortlist_id, channel_id, status) values (1,'UC_A','replied'),(1,'UC_C','contacted'),(1,'UC_B','to_contact')" \
    -c "select * from shortlist_funnel" -c "select * from creator_upload_cadence" -c "select * from creator_growth_30d"
echo "ALL SQL TESTS RAN"

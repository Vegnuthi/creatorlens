-- TEST ONLY: a stand-in for pgvector so the SQL logic can be tested on plain Postgres.
-- Production uses the real pgvector extension (enabled by default on Supabase).
create domain vector as float8[];

create or replace function cosine_distance(a float8[], b float8[]) returns float8
language sql immutable as $$
    select 1 - sum(x * y) / (sqrt(sum(x * x)) * sqrt(sum(y * y)))
    from unnest(a, b) as t(x, y)
$$;

create operator <=> (leftarg = float8[], rightarg = float8[], function = cosine_distance);

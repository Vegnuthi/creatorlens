-- Approximate nearest-neighbour index for semantic search (cosine distance)
create index if not exists content_chunks_embedding_hnsw
    on content_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists content_chunks_channel_idx on content_chunks (channel_id);
create index if not exists videos_channel_published_idx on videos (channel_id, published_at desc);
create index if not exists creators_subs_idx on creators (subscriber_count);
create index if not exists search_logs_created_idx on search_logs (created_at desc);

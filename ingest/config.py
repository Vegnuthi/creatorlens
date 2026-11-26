import os
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Must match the vector(384) size in db/01_schema.sql
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_DIM = 384

CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "150"))

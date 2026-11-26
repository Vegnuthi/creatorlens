"""API test against the shim database built by tests/test_sql.sh.

    bash tests/test_sql.sh && TEST_DATABASE_URL=... python tests/test_api.py
The embedding model is replaced by a fixed 4-number vector that matches the test data.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql://postgres@/creatorlens_test?host=/tmp/pgtest&port=5433")

import app.main as m  # noqa: E402
import ingest.config  # noqa: E402

m.DATABASE_URL = os.environ["DATABASE_URL"]
m.embed_query = lambda text: [1.0, 0.2, 0.0, 0.0]   # fake "placement prep" vector
m.configure_connection = lambda conn: None           # shim has no pgvector type to register

from fastapi.testclient import TestClient  # noqa: E402

with TestClient(m.app) as c:
    assert c.get("/").status_code == 200

    r = c.post("/api/search", json={"query": "placement interview prep"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    print("search:", [(x["title"], float(x["match_score"])) for x in results])
    assert results[-1]["title"] == "Cook With Me", "off-topic creator should rank last"

    r = c.post("/api/search", json={"query": "placement interview prep", "max_subs": 100000, "language": "en"})
    assert [x["title"] for x in r.json()["results"]] == ["DSA With Dev"]

    assert c.post("/api/search", json={"query": "x"}).status_code == 422

    d = c.get("/api/creators/UC_A").json()
    print("detail:", d["title"], d["growth_pct"], d["avg_days_between_uploads"], len(d["recent_videos"]))
    assert c.get("/api/creators/nope").status_code == 404

    sl = c.post("/api/shortlists", json={"name": "Test campaign"}).json()
    sid = sl["id"]
    assert c.post(f"/api/shortlists/{sid}/creators", json={"channel_id": "UC_A", "match_score": 80}).status_code == 201
    assert c.post(f"/api/shortlists/{sid}/creators", json={"channel_id": "UC_C", "match_score": 70}).status_code == 201
    assert c.patch(f"/api/shortlists/{sid}/creators/UC_A", json={"status": "replied"}).json()["status"] == "replied"
    assert c.patch(f"/api/shortlists/{sid}/creators/UC_A", json={"status": "bogus"}).status_code == 422
    lists = c.get("/api/shortlists").json()
    mine = next(x for x in lists if x["id"] == sid)
    print("funnel:", {k: mine[k] for k in ("total", "to_contact", "replied", "reply_rate_pct")})
    csv_text = c.get(f"/api/shortlists/{sid}/export.csv").text
    print("csv:\n" + csv_text)
    assert "Placement Guru" in csv_text
    assert c.delete(f"/api/shortlists/{sid}/creators/UC_C").status_code == 204
    assert len(c.get(f"/api/shortlists/{sid}").json()["creators"]) == 1

    ins = c.get("/api/insights").json()
    print("insights:", ins["totals"], ins["top_searches"][:1])
    assert ins["totals"]["searches"] >= 2

print("ALL API TESTS PASSED")

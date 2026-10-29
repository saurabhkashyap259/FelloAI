"""A stored score can be recomputed exactly from stored data and the referenced model (FR-002)."""

from sqlalchemy import text

from tests.helpers import scored_worlds
from worklist.db.engine import job_engine, tenant_scope
from worklist.model.score import recompute_score


def test_recomputed_scores_match_stored():
    (acme,) = scored_worlds(1)
    with tenant_scope(job_engine(), acme["tenant"]["id"]) as conn:
        rows = conn.execute(text("SELECT id, probability, raw_score, contributions FROM account_score "
                                 "WHERE state = 'scored'")).mappings().all()
        assert rows
        for row in rows:
            again = recompute_score(conn, row["id"])
            assert abs(again["probability"] - row["probability"]) < 1e-9
            assert abs(again["raw_score"] - row["raw_score"]) < 1e-9
            assert again["contributions"] == row["contributions"]

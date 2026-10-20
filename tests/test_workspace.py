import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import database
from app.api import workspace
from app.api.routes import require_operator
from app.csv_ingestion import import_upload
from app.migrate import migrate


@pytest.fixture
def db(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    schema = "test_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    monkeypatch.setattr(
        database,
        "_connect",
        lambda: psycopg.connect(
            url, options=f"-c search_path={schema},public", row_factory=dict_row
        ),
    )
    migrate()
    try:
        yield
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[require_operator] = lambda: "operator"
    return TestClient(app)


def seed_run(tmp_path):
    upload_id = str(uuid.uuid4())
    path = tmp_path / "workspace.csv"
    ids = ["a/matched", "b-review", "c-unmatched", "d-partial", "e-empty", "literal_%", "literalXX"]
    path.write_text(
        "user_id,name,email,monthly_income,credit_score,employment_status,age\n"
        + "".join(f"{uid},Fixture,fixture@example.invalid,50000,780,Salaried,30\n" for uid in ids)
    )
    with database.connection() as conn:
        conn.execute(
            "INSERT INTO upload(upload_id,object_key,filename,status) VALUES(%s,%s,'workspace.csv','queued')",
            (upload_id, upload_id),
        )
    run = import_upload(upload_id, upload_id, path.name, path)
    with database.connection() as conn:
        products = conn.execute(
            "SELECT product_id,snapshot_id FROM run_policy WHERE run_id=%s ORDER BY product_id",
            (run.run_id,),
        ).fetchall()
        assert len(products) == 2
        for uid, decisions in [
            ("a/matched", ["eligible", "review"]),
            ("b-review", ["review", "ineligible"]),
            ("c-unmatched", ["ineligible", "ineligible"]),
            ("d-partial", ["eligible"]),
            ("literal_%", ["eligible", "eligible"]),
            ("literalXX", ["ineligible", "ineligible"]),
        ]:
            for product, decision in zip(products, decisions, strict=False):
                conn.execute(
                    'INSERT INTO eligibility_result(run_id,user_id,product_id,snapshot_id,decision,reasons,rule_version) VALUES(%s,%s,%s,%s,%s,\'[{"criterion":"age","status":"pass"}]\',\'1\')',
                    (run.run_id, uid, product["product_id"], product["snapshot_id"], decision),
                )
    return run.run_id, products


def test_grouped_statuses_progress_and_pagination(db, tmp_path, client):
    run_id, products = seed_run(tmp_path)
    response = client.get(f"/api/runs/{run_id}/applicants")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 7
    assert [a["status"] for a in data["items"]] == [
        "matched",
        "review",
        "unmatched",
        "processing",
        "processing",
        "unmatched",
        "matched",
    ]
    assert len(data["items"][0]["matched_products"]) == 1
    assert (
        client.get(f"/api/runs/{run_id}/applicants?limit=2&offset=2").json()["items"]
        == data["items"][2:4]
    )
    progress = client.get(f"/api/runs/{run_id}/progress").json()
    assert progress["product_count"] == 2
    assert progress["processed_decisions"] == 11
    assert progress["processed_applicants"] == 5
    assert progress["applicant_summary"] == {
        "matched": 2,
        "review": 1,
        "unmatched": 2,
        "processing": 2,
    }
    assert progress["filename"] == "workspace.csv"
    filtered = client.get(
        f"/api/runs/{run_id}/applicants",
        params={"status": "matched", "matched_product_id": products[0]["product_id"]},
    ).json()
    assert filtered["total"] == 2
    assert (
        client.get(f"/api/runs/{run_id}/progress").json()["applicant_summary"]
        == progress["applicant_summary"]
    )
    # Eligible partial applicants can be selected by product without being marked fully matched.
    assert (
        client.get(
            f"/api/runs/{run_id}/applicants",
            params={"matched_product_id": products[0]["product_id"]},
        ).json()["total"]
        == 3
    )


def test_literal_prefix_detail_and_run_list(db, tmp_path, client):
    run_id, _ = seed_run(tmp_path)
    found = client.get(f"/api/runs/{run_id}/applicants", params={"q": "literal_%"}).json()
    assert found["total"] == 1
    assert found["items"][0]["user_id"] == "literal_%"
    detail = client.get(f"/api/runs/{run_id}/applicant", params={"user_id": "a/matched"})
    assert detail.status_code == 200
    assert len(detail.json()["results"]) == 2
    assert {
        "source_url",
        "snapshot_id",
        "rule_version",
        "observed_at",
        "evaluated_at",
        "reasons",
    } <= detail.json()["results"][0].keys()
    assert (
        client.get(f"/api/runs/{run_id}/applicant", params={"user_id": "missing"}).status_code
        == 404
    )
    listed = client.get("/api/runs?limit=1").json()
    assert listed["total"] == 1
    assert listed["items"][0]["filename"] == "workspace.csv"
    assert client.get(f"/api/runs/{uuid.uuid4()}/progress").status_code == 404


@pytest.mark.parametrize(
    "query", ["limit=101", "limit=0", "offset=-1", "offset=100001", "status=eligible"]
)
def test_query_bounds(client, query):
    assert client.get(f"/api/runs/{uuid.uuid4()}/applicants?{query}").status_code == 422


def test_operator_auth_required():
    app = FastAPI()
    app.include_router(workspace.router)
    client = TestClient(app)
    for path in [
        "/api/runs",
        f"/api/runs/{uuid.uuid4()}/progress",
        f"/api/runs/{uuid.uuid4()}/notifications",
    ]:
        assert client.get(path).status_code == 401


def test_explicit_workspace_deep_links():
    from app.main import app

    if not (Path(__file__).resolve().parents[1] / "web/dist/index.html").is_file():
        pytest.skip("Build web/dist to check served workspace pages")
    client = TestClient(app)
    for path in ["/", "/overview", "/applicants", "/policies"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
    for path in ["/api/not-a-route", "/assets/not-a-file.js", "/not-a-page"]:
        response = client.get(path)
        assert response.status_code == 404
        assert "text/html" not in response.headers.get("content-type", "")


def test_notification_status_summary(db, tmp_path, client):
    run_id, _ = seed_run(tmp_path)
    assert client.get(f"/api/runs/{run_id}/notifications").json() == {
        "preview_ready": 0,
        "sent": 0,
        "pending": 0,
        "needs_attention": 0,
    }
    statuses = ["preview", "ready", "claimed", "sending", "sent", "uncertain", "failed"]
    with database.connection() as conn:
        ids = conn.execute(
            "SELECT user_id FROM batch_user WHERE run_id=%s ORDER BY user_id", (run_id,)
        ).fetchall()
        for user, status in zip(ids, statuses, strict=True):
            conn.execute(
                "INSERT INTO notification_delivery(run_id,user_id,mode,status) VALUES(%s,%s,'preview',%s)",
                (run_id, user["user_id"], status),
            )
    assert client.get(f"/api/runs/{run_id}/notifications").json() == {
        "preview_ready": 1,
        "sent": 1,
        "pending": 3,
        "needs_attention": 2,
    }

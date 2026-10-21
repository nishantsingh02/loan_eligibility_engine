import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.rows import dict_row

from app import database, relay
from app.api.notifications import (
    ClaimBatch,
    DeliveryClaim,
    DeliveryFailure,
    DeliveryReceipt,
    begin_delivery,
    claim_notifications,
    prepare_notifications,
    record_failure,
    record_receipt,
)
from app.api.routes import (
    NextBatchRequest,
    PersistBatchRequest,
    PolicyProposalRequest,
    next_match_batch,
    policy_proposal,
    save_match_batch,
)
from app.config import get_settings
from app.csv_ingestion import import_upload
from app.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]


def evaluate(page):
    workflow = json.loads(
        (ROOT / "n8n/workflows/02-match-applicants.json").read_text(encoding="utf-8")
    )
    code = next(n["parameters"]["jsCode"] for n in workflow["nodes"] if n["type"].endswith(".code"))
    result = subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('fs');const {code,page}=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(new Function('$input',code)({first:()=>({json:page})})[0].json));",
        ],
        input=json.dumps({"code": code, "page": page}, default=str),
        text=True,
        capture_output=True,
        check=True,
    )
    return PersistBatchRequest.model_validate_json(result.stdout)


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
    monkeypatch.setattr(get_settings(), "notification_mode", "preview")
    try:
        yield
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def imported_run(tmp_path, count=205):
    upload_id = str(uuid.uuid4())
    path = tmp_path / "synthetic.csv"
    path.write_text(
        "user_id,name,email,monthly_income,credit_score,employment_status,age\n"
        + "".join(
            f"user-{i:05},Synthetic Applicant,fixture@example.invalid,50000,780,Salaried,30\n"
            for i in range(count)
        )
    )
    with database.connection() as conn:
        conn.execute(
            "INSERT INTO upload (upload_id,object_key,filename,status) VALUES (%s,%s,'synthetic.csv','queued')",
            (upload_id, upload_id),
        )
    return import_upload(upload_id, upload_id, path.name, path)


def test_three_products_multiple_pages_pinned_versions_and_notifications(db, tmp_path):
    with database.connection() as conn:
        conn.execute(
            "INSERT INTO product (product_id,lender,product_name,product_url) VALUES ('third','Synthetic','Fixture','https://example.invalid')"
        )
        snapshot = conn.execute(
            "INSERT INTO policy_snapshot (product_id,source_url,source_digest,model_provider,model_name,extraction_status,rules,source_excerpt) SELECT 'third',source_url,'fixture','fixture','fixture','approved',rules,source_excerpt FROM policy_snapshot WHERE product_id='axis-personal-salaried' RETURNING snapshot_id"
        ).fetchone()
        conn.execute(
            "UPDATE product SET current_snapshot_id=%s WHERE product_id='third'",
            (snapshot["snapshot_id"],),
        )
    run = imported_run(tmp_path)
    page = next_match_batch(NextBatchRequest(run_id=run.run_id))
    assert len(page["users"]) == 133
    batch = evaluate(page)
    assert len(batch.results) == 399
    # A policy changing between fetch and save must not drop or alter this run.
    with database.connection() as conn:
        conn.execute("UPDATE product SET current_snapshot_id=NULL WHERE product_id='third'")
    first = save_match_batch(batch)
    assert first["saved"] == 399
    assert save_match_batch(batch)["saved"] == 0
    second = evaluate(
        next_match_batch(NextBatchRequest(run_id=run.run_id, cursor_user_id=batch.last_user_id))
    )
    assert save_match_batch(second)["status"] == "completed"
    with database.connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM eligibility_result WHERE run_id=%s", (run.run_id,)
            ).fetchone()["n"]
            == 615
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM outbox WHERE aggregate_id=%s AND event_type='notification_preview'",
                (run.run_id,),
            ).fetchone()["n"]
            == 1
        )
    preview = prepare_notifications(run.run_id)
    assert preview["eligible_matches"] == 410
    assert preview["delivery_enabled"] is False
    assert prepare_notifications(run.run_id)["eligible_matches"] == 410


def test_incomplete_batch_rolls_back(db, tmp_path):
    from fastapi import HTTPException

    run = imported_run(tmp_path, 2)
    batch = evaluate(next_match_batch(NextBatchRequest(run_id=run.run_id)))
    batch.results.pop()
    with pytest.raises(HTTPException, match="expected applicant-policy"):
        save_match_batch(batch)
    assert database.one_row("SELECT COUNT(*) AS n FROM eligibility_result")["n"] == 0


def test_policy_proposal_binds_audit_strings(db):
    rules = database.one_row(
        "SELECT rules FROM policy_snapshot WHERE product_id='axis-personal-salaried'"
    )["rules"]
    rules["monthly_income"]["location_dependent"] = False
    result = policy_proposal(
        PolicyProposalRequest(
            product_id="axis-personal-salaried",
            source_excerpt="Synthetic source evidence for this integration fixture; advertised rate 10% per year.",
            provider="openrouter",
            model_name="fixture",
            rules=rules,
            evidence=[],
            limitations=[],
            advertised_rate="10% per year",
            rate_source_quote="advertised rate 10% per year",
        )
    )
    assert result["status"] == "pending_review"


def test_test_delivery_uses_controlled_destination_and_claims_once(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "notification_mode", "test")
    monkeypatch.setattr(get_settings(), "test_recipients", "operator@controlled.invalid")
    monkeypatch.setattr(get_settings(), "ses_sender", "sender@controlled.invalid")
    run = imported_run(tmp_path, 3)
    save_match_batch(evaluate(next_match_batch(NextBatchRequest(run_id=run.run_id))))
    prepared = prepare_notifications(run.run_id)
    assert prepared["delivery_enabled"] is True
    assert prepared["prepared_applicants"] == 3
    assert prepare_notifications(run.run_id)["prepared_applicants"] == 3
    claims = claim_notifications(ClaimBatch())["deliveries"]
    assert len(claims) == 3
    assert claim_notifications(ClaimBatch())["deliveries"] == []
    assert "fixture@example.invalid" not in json.dumps(claims)
    for claim in claims:
        assert claim["products"][0]["lender"] == "Axis Bank"
        # Advance the fake rate gate without sleeping or contacting SES.
        with database.connection() as conn:
            conn.execute("UPDATE notification_send_gate SET next_send_at=now()-interval '1 second'")
        started = begin_delivery(uuid.UUID(claim["delivery_id"]), DeliveryClaim(**claim))
        assert started["recipient"] == "operator@controlled.invalid"
        assert started["delivery_enabled"] is True
        assert (
            begin_delivery(uuid.UUID(claim["delivery_id"]), DeliveryClaim(**claim))[
                "delivery_enabled"
            ]
            is False
        )
        receipt = DeliveryReceipt(**claim, message_id="synthetic-ses-" + claim["delivery_id"])
        assert record_receipt(receipt)["status"] == "sent"
        assert record_receipt(receipt)["status"] == "sent"


def test_preview_mode_is_frozen_before_enabling_test_delivery(db, tmp_path, monkeypatch):
    run = imported_run(tmp_path, 2)
    save_match_batch(evaluate(next_match_batch(NextBatchRequest(run_id=run.run_id))))
    monkeypatch.setattr(get_settings(), "notification_mode", "test")
    monkeypatch.setattr(get_settings(), "test_recipients", "operator@controlled.invalid")
    monkeypatch.setattr(get_settings(), "ses_sender", "sender@controlled.invalid")
    assert prepare_notifications(run.run_id)["delivery_enabled"] is False
    assert claim_notifications(ClaimBatch())["deliveries"] == []


def test_missing_receipt_is_uncertain_and_never_automatically_resent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "notification_mode", "test")
    monkeypatch.setattr(get_settings(), "test_recipients", "operator@controlled.invalid")
    monkeypatch.setattr(get_settings(), "ses_sender", "sender@controlled.invalid")
    run = imported_run(tmp_path, 1)
    save_match_batch(evaluate(next_match_batch(NextBatchRequest(run_id=run.run_id))))
    prepare_notifications(run.run_id)
    claim = claim_notifications(ClaimBatch())["deliveries"][0]
    delivery_id = uuid.UUID(claim["delivery_id"])
    assert begin_delivery(delivery_id, DeliveryClaim(**claim))["delivery_enabled"]
    with database.connection() as conn:
        conn.execute("UPDATE notification_delivery SET lease_until=now()-interval '1 minute'")
    assert claim_notifications(ClaimBatch())["deliveries"] == []
    assert database.one_row("SELECT status FROM notification_delivery")["status"] == "uncertain"
    assert record_receipt(DeliveryReceipt(**claim, message_id="late-receipt"))["status"] == "sent"


def test_delivery_gate_enforces_aliases_caps_and_definitive_failure(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "notification_mode", "test")
    monkeypatch.setattr(get_settings(), "notification_run_limit", 1)
    monkeypatch.setattr(get_settings(), "test_recipients", "operator@controlled.invalid")
    monkeypatch.setattr(get_settings(), "ses_sender", "sender@controlled.invalid")
    run = imported_run(tmp_path, 3)
    save_match_batch(evaluate(next_match_batch(NextBatchRequest(run_id=run.run_id))))
    prepare_notifications(run.run_id)
    first, second, third = claim_notifications(ClaimBatch())["deliveries"]
    assert begin_delivery(uuid.UUID(first["delivery_id"]), DeliveryClaim(**first))[
        "delivery_enabled"
    ]
    result = begin_delivery(uuid.UUID(second["delivery_id"]), DeliveryClaim(**second))
    assert result["reason"] == "run_limit"
    assert (
        record_failure(
            uuid.UUID(first["delivery_id"]),
            DeliveryFailure(**first, outcome="rejected", error_code="throttled"),
        )["status"]
        == "ready"
    )
    monkeypatch.setattr(get_settings(), "test_recipients", "changed@controlled.invalid")
    result = begin_delivery(uuid.UUID(third["delivery_id"]), DeliveryClaim(**third))
    assert result["reason"] == "recipient_not_allowed"


def test_import_database_errors_remain_retryable(tmp_path, monkeypatch):
    from app import csv_ingestion

    path = tmp_path / "synthetic.csv"
    path.write_text(
        "user_id,name,email,monthly_income,credit_score,employment_status,age\na,Synthetic,fixture@example.invalid,50000,780,Salaried,30\n"
    )

    @contextmanager
    def unavailable():
        raise psycopg.OperationalError("temporary database outage")
        yield

    monkeypatch.setattr(csv_ingestion, "connection", unavailable)
    with pytest.raises(psycopg.OperationalError):
        import_upload(str(uuid.uuid4()), "fixture", path.name, path)


def test_relay_terminal_failure_records_run_failure(monkeypatch):
    event = {
        "outbox_id": 1,
        "event_type": "match_run",
        "aggregate_id": str(uuid.uuid4()),
        "payload": {},
        "attempt_count": 9,
    }
    cursor = MagicMock()
    cursor.execute.return_value.fetchone.return_value = event
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    context = MagicMock()
    context.__enter__.return_value = conn
    monkeypatch.setattr(relay, "connection", lambda: context)
    client = MagicMock()
    client.__enter__.return_value.post.side_effect = ConnectionError("fixture failure")
    monkeypatch.setattr(relay.httpx, "Client", lambda **kwargs: client)
    assert relay.deliver_once()
    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("SELECT outbox_id,event_type,aggregate_id" in s for s in statements)
    assert any("UPDATE run SET status='failed'" in s for s in statements)

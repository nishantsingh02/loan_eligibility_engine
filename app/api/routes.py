import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.config import get_settings
from app.csv_ingestion import ImportErrorCode, import_upload
from app.database import _secret, all_rows, connection, one_row
from app.matching import MAX_DECISIONS, PAGE_QUERY

router = APIRouter()


def require_operator(x_operator_token: str | None = Header(default=None)) -> str:
    settings = get_settings()
    token = (
        _secret(settings.operator_token_secret_arn)
        if settings.operator_token_secret_arn
        else settings.operator_token
    )
    if not x_operator_token or x_operator_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return "operator"


def require_internal(x_internal_token: str | None = Header(default=None)) -> str:
    settings = get_settings()
    token = (
        _secret(settings.internal_token_secret_arn)
        if settings.internal_token_secret_arn
        else settings.internal_api_token
    )
    if not x_internal_token or x_internal_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return "workflow"


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "applicants.csv").name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100]
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def _import_job(upload_id: str, object_key: str, filename: str, path: Path) -> None:
    try:
        import_upload(upload_id, object_key, filename, path)
    except ImportErrorCode as exc:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE upload SET status='failed', error_code=%s, completed_at=now() WHERE upload_id=%s",
                (exc.code, upload_id),
            )


class NextBatchRequest(BaseModel):
    run_id: str
    cursor_user_id: str = ""


class ResultRow(BaseModel):
    user_id: str = Field(max_length=128)
    product_id: str = Field(max_length=100)
    snapshot_id: str
    decision: str
    reasons: list[dict[str, Any]] = Field(max_length=24)
    rule_version: str = Field(max_length=40)


class PersistBatchRequest(BaseModel):
    run_id: str
    results: list[ResultRow] = Field(max_length=400)
    last_user_id: str = Field(max_length=128)
    has_more: bool
    cursor_user_id: str = Field(default="", max_length=128)


class PolicySourceRequest(BaseModel):
    product_id: str = Field(max_length=100)
    source_excerpt: str = Field(min_length=40, max_length=24_000)


class PolicyProposalRequest(PolicySourceRequest):
    provider: str = Field(pattern="^(openrouter|google)$")
    model_name: str = Field(min_length=1, max_length=100)
    rules: dict[str, Any]
    advertised_rate: str | None = Field(default=None, max_length=500)
    rate_source_quote: str | None = Field(default=None, max_length=1000)
    evidence: list[dict[str, str]] = Field(max_length=12)
    limitations: list[str] = Field(max_length=12)


def _validate_rules(rules: dict[str, Any]) -> None:
    if set(rules) != {
        "version",
        "employment",
        "age",
        "monthly_income",
        "credit_score",
        "incomplete_eligibility",
    }:
        raise HTTPException(status_code=422, detail="Policy uses an unsupported rule schema")
    if rules.get("version") != "1" or rules.get("incomplete_eligibility") != "review":
        raise HTTPException(status_code=422, detail="Unsupported eligibility rule version")
    expected = {
        "employment": {"required": {"status"}, "unknown": set()},
        "age": {"required": {"min", "max"}, "unknown": set()},
        "monthly_income": {"required": {"min", "currency", "location_dependent"}, "unknown": set()},
        "credit_score": {"required": {"min"}, "preferred": {"min"}, "unknown": set()},
    }
    for name, variants in expected.items():
        value = rules.get(name)
        if not isinstance(value, dict) or value.get("kind") not in variants:
            raise HTTPException(status_code=422, detail=f"Invalid {name} criteria")
        if set(value) != {"kind", *variants[value["kind"]]}:
            raise HTTPException(status_code=422, detail=f"Invalid {name} criteria fields")
        if (
            name == "employment"
            and value["kind"] == "required"
            and value["status"] not in {"Salaried", "Self-Employed", "Business"}
        ):
            raise HTTPException(status_code=422, detail="Unknown employment status")
        if name == "age" and value["kind"] == "required":
            minimum, maximum = value.get("min"), value.get("max")
            if (
                isinstance(minimum, bool)
                or isinstance(maximum, bool)
                or not isinstance(minimum, int)
                or not isinstance(maximum, int)
                or not 18 <= minimum <= maximum <= 100
            ):
                raise HTTPException(status_code=422, detail="Invalid age range")
        if name in {"monthly_income", "credit_score"} and value["kind"] in {
            "required",
            "preferred",
        }:
            cutoff = value.get("min")
            if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or cutoff < 0:
                raise HTTPException(status_code=422, detail=f"Invalid {name} cutoff")
        if (
            name == "monthly_income"
            and value["kind"] == "required"
            and (value["currency"] != "INR" or not isinstance(value["location_dependent"], bool))
        ):
            raise HTTPException(status_code=422, detail="Unknown income criteria")


@router.get("/health")
def health() -> dict[str, str]:
    release_path = Path(__file__).resolve().parents[1] / "release.json"
    release = json.loads(release_path.read_text()) if release_path.exists() else {}
    return {"status": "ok", "revision": release.get("commit", "development")}


@router.get("/api/dashboard")
def dashboard(_: str = Depends(require_operator)) -> dict[str, Any]:
    runs = all_rows(
        "SELECT r.run_id::text, r.status, r.row_count, r.duplicate_email_count, r.matched_count, r.unmatched_count, r.review_count, r.created_at, r.started_at, r.completed_at, r.error_code, (SELECT COUNT(*) FROM run_policy rp WHERE rp.run_id=r.run_id) AS product_count, (SELECT COUNT(*) FROM eligibility_result e WHERE e.run_id=r.run_id) AS processed_decisions "
        "FROM run r ORDER BY r.created_at DESC LIMIT 8"
    )
    counts = one_row(
        "SELECT COUNT(*) AS total_runs, COALESCE(SUM(row_count),0) AS total_rows FROM run"
    )
    products = all_rows(
        "SELECT p.product_id,p.lender,p.product_name,p.product_url,s.observed_at,s.extraction_status,s.advertised_rate "
        "FROM product p LEFT JOIN policy_snapshot s ON s.snapshot_id=p.current_snapshot_id WHERE p.active ORDER BY p.lender"
    )
    queue = one_row(
        "SELECT COUNT(*) FILTER (WHERE status IN ('pending','processing')) AS pending, COUNT(*) FILTER (WHERE status='failed') AS failed FROM outbox"
    )
    heartbeat = one_row(
        "SELECT last_seen_at>now()-interval '90 seconds' AS online FROM service_heartbeat WHERE service='relay'"
    )
    return {
        "runs": runs,
        "total_runs": counts["total_runs"],
        "total_rows": counts["total_rows"],
        "products": products,
        "pending_events": queue["pending"],
        "failed_events": queue["failed"],
        "relay_online": bool(heartbeat and heartbeat["online"]),
        "storage_mode": "s3" if get_settings().s3_upload_bucket else "local",
        "max_upload_bytes": get_settings().max_upload_bytes,
    }


@router.get("/api/policies/pending")
def pending_policies(_: str = Depends(require_operator)) -> list[dict[str, Any]]:
    return all_rows(
        "SELECT s.snapshot_id::text,s.product_id,p.lender,p.product_name,s.source_url,s.observed_at,s.rules,s.source_excerpt,s.model_provider,s.model_name,s.reviewer_note,s.advertised_rate,s.rate_source_quote "
        "FROM policy_snapshot s JOIN product p ON p.product_id=s.product_id WHERE s.extraction_status='pending_review' ORDER BY s.observed_at DESC"
    )


@router.post("/api/policies/{snapshot_id}/approve")
def approve_policy(snapshot_id: str, _: str = Depends(require_operator)) -> dict[str, str]:
    with connection() as conn, conn.cursor() as cur:
        snapshot = cur.execute(
            "SELECT snapshot_id,product_id,observed_at,extraction_status FROM policy_snapshot WHERE snapshot_id=%s FOR UPDATE",
            (snapshot_id,),
        ).fetchone()
        if not snapshot or snapshot["extraction_status"] != "pending_review":
            raise HTTPException(status_code=404, detail="Pending policy not found")
        current = cur.execute(
            "SELECT current_snapshot_id FROM product WHERE product_id=%s FOR UPDATE",
            (snapshot["product_id"],),
        ).fetchone()
        if current and current["current_snapshot_id"]:
            latest = cur.execute(
                "SELECT observed_at FROM policy_snapshot WHERE snapshot_id=%s",
                (current["current_snapshot_id"],),
            ).fetchone()
            if latest and latest["observed_at"] > snapshot["observed_at"]:
                raise HTTPException(status_code=409, detail="A newer policy is already active")
        cur.execute(
            "UPDATE policy_snapshot SET extraction_status='approved', reviewer_note='Approved by an operator after checking the linked official source.' WHERE snapshot_id=%s",
            (snapshot_id,),
        )
        cur.execute(
            "UPDATE product SET current_snapshot_id=%s,updated_at=now() WHERE product_id=%s",
            (snapshot_id, snapshot["product_id"]),
        )
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id) VALUES ('operator','policy_approved','snapshot',%s)",
            (snapshot_id,),
        )
    return {"status": "approved", "snapshot_id": snapshot_id}


@router.post("/api/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(
    filename: str = Query(default="applicants.csv", max_length=180),
    _: str = Depends(require_operator),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.s3_upload_bucket:
        raise HTTPException(
            status_code=503,
            detail="Direct cloud uploads are not configured. Use the local upload route.",
        )
    upload_id = str(uuid.uuid4())
    object_key = f"incoming/{upload_id}.csv"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO upload (upload_id, object_key, filename, status) VALUES (%s,%s,%s,'awaiting_upload')",
            (upload_id, object_key, _safe_filename(filename)),
        )
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        config=Config(s3={"addressing_style": "virtual"}),
    )
    try:
        signed = client.generate_presigned_post(
            Bucket=settings.s3_upload_bucket,
            Key=object_key,
            Fields={"Content-Type": "text/csv"},
            Conditions=[
                {"Content-Type": "text/csv"},
                ["content-length-range", 1, settings.max_upload_bytes],
            ],
            ExpiresIn=300,
        )
    except Exception as exc:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE upload SET status='failed',error_code='upload_signing_failed' WHERE upload_id=%s",
                (upload_id,),
            )
        raise HTTPException(status_code=503, detail="Upload service unavailable") from exc
    return {
        "upload_id": upload_id,
        "method": "POST",
        "upload": signed,
        "max_bytes": settings.max_upload_bytes,
        "expires_in_seconds": 300,
    }


@router.post("/api/uploads/local", status_code=status.HTTP_202_ACCEPTED)
async def upload_local(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _: str = Depends(require_operator),
) -> dict[str, Any]:
    settings = get_settings()
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file.")
    upload_id = str(uuid.uuid4())
    object_key = f"local/{upload_id}.csv"
    destination_dir = Path(settings.local_upload_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f"{upload_id}-", suffix=".csv", dir=destination_dir)
    path = Path(raw_path)
    size = 0
    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413, detail="CSV exceeds the 25 MiB upload limit."
                    )
                destination.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO upload (upload_id,object_key,filename,status) VALUES (%s,%s,%s,'queued')",
            (upload_id, object_key, _safe_filename(file.filename)),
        )
    background_tasks.add_task(
        _import_job, upload_id, object_key, _safe_filename(file.filename), path
    )
    return {"upload_id": upload_id, "status": "queued"}


@router.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str, _: str = Depends(require_operator)) -> dict[str, Any]:
    row = one_row(
        "SELECT upload_id::text,status,run_id::text,error_code,created_at,completed_at FROM upload WHERE upload_id=%s",
        (upload_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    return row


@router.get("/api/runs/{run_id}")
def run_status(run_id: str, _: str = Depends(require_operator)) -> dict[str, Any]:
    row = one_row(
        "SELECT run_id::text,upload_id::text,status,row_count,distinct_users,duplicate_email_count,matched_count,unmatched_count,review_count,error_code,created_at,started_at,completed_at,prefiltered_count,candidate_count,notification_mode FROM run WHERE run_id=%s",
        (run_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


@router.post("/api/runs/{run_id}/retry")
def retry_run(run_id: str, _: str = Depends(require_operator)) -> dict[str, str]:
    with connection() as conn, conn.cursor() as cur:
        run = cur.execute("SELECT status FROM run WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run["status"] != "failed":
            raise HTTPException(status_code=409, detail="Only a failed run can be retried")
        event = cur.execute(
            "SELECT outbox_id FROM outbox WHERE aggregate_id=%s AND event_type='match_run' AND status='failed' ORDER BY outbox_id DESC LIMIT 1 FOR UPDATE",
            (run_id,),
        ).fetchone()
        if not event:
            raise HTTPException(
                status_code=409, detail="This failure needs an operator investigation before retry"
            )
        cur.execute(
            "UPDATE outbox SET status='pending',attempt_count=0,next_attempt_at=now(),last_error_code=NULL,locked_at=NULL WHERE outbox_id=%s",
            (event["outbox_id"],),
        )
        cur.execute(
            "UPDATE run SET status='queued',error_code=NULL,completed_at=NULL WHERE run_id=%s",
            (run_id,),
        )
        cur.execute(
            "UPDATE upload SET status='queued',error_code=NULL,completed_at=NULL WHERE run_id=%s",
            (run_id,),
        )
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id) VALUES ('operator','run_retried','run',%s)",
            (run_id,),
        )
    return {"status": "queued"}


@router.get("/api/runs/{run_id}/results")
def run_results(
    run_id: str,
    decision: str | None = None,
    product_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    _: str = Depends(require_operator),
) -> dict[str, Any]:
    if decision and decision not in {"eligible", "ineligible", "review"}:
        raise HTTPException(status_code=422, detail="Unknown decision filter")
    row = one_row("SELECT run_id FROM run WHERE run_id=%s", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    where = ["e.run_id=%s"]
    params: list[Any] = [run_id]
    if decision:
        where.append("e.decision=%s")
        params.append(decision)
    if product_id:
        where.append("e.product_id=%s")
        params.append(product_id)
    clause = " AND ".join(where)
    total = one_row(f"SELECT COUNT(*) AS total FROM eligibility_result e WHERE {clause}", params)[
        "total"
    ]
    results = all_rows(
        "SELECT e.result_id::text,e.user_id,e.product_id,p.lender,p.product_name,e.decision,e.reasons,e.rule_version,e.evaluated_at,e.snapshot_id::text,s.source_url,s.observed_at,s.advertised_rate "
        f"FROM eligibility_result e JOIN product p ON p.product_id=e.product_id JOIN policy_snapshot s ON s.snapshot_id=e.snapshot_id WHERE {clause} "
        "ORDER BY e.user_id,e.product_id LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    return {"total": total, "limit": limit, "offset": offset, "results": results}


@router.post("/api/runs/{run_id}/notification-preview")
def notification_preview(run_id: str, _: str = Depends(require_operator)) -> dict[str, Any]:
    run = one_row("SELECT run_id,status FROM run WHERE run_id=%s", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] != "completed":
        raise HTTPException(
            status_code=409, detail="Wait for the run to finish before previewing notifications"
        )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_event (run_id,user_id,product_id,channel,mode,status,idempotency_key) "
            "SELECT e.run_id,e.user_id,e.product_id,'email','preview','previewed',e.run_id::text||':'||e.user_id||':'||e.product_id||':preview' "
            "FROM eligibility_result e WHERE e.run_id=%s AND e.decision='eligible' ON CONFLICT (idempotency_key) DO NOTHING",
            (run_id,),
        )
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id) VALUES ('operator','notification_previewed','run',%s)",
            (run_id,),
        )
    result = one_row(
        "SELECT COUNT(*) AS matches,COUNT(DISTINCT lower(u.email)) AS unique_addresses "
        "FROM notification_event n JOIN batch_user u ON u.run_id=n.run_id AND u.user_id=n.user_id "
        "WHERE n.run_id=%s AND n.status='previewed' AND n.mode='preview'",
        (run_id,),
    )
    return {
        "mode": "preview",
        "eligible_matches": result["matches"],
        "distinct_email_addresses": result["unique_addresses"],
        "delivery_enabled": False,
        "note": "Preview only. No applicant emails are sent from this project.",
    }


@router.post("/internal/v1/next-match-batch", dependencies=[Depends(require_internal)])
def next_match_batch(body: NextBatchRequest) -> dict[str, Any]:
    with connection() as conn, conn.cursor() as cur:
        run = cur.execute("SELECT status FROM run WHERE run_id=%s", (body.run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return cur.execute(
            PAGE_QUERY, {"run_id": body.run_id, "cursor": body.cursor_user_id}
        ).fetchone()


@router.post("/internal/v1/policy-source", dependencies=[Depends(require_internal)])
def policy_source(body: PolicySourceRequest) -> dict[str, Any]:
    source_digest = hashlib.sha256(body.source_excerpt.encode("utf-8")).hexdigest()
    product = one_row(
        "SELECT product_url FROM product WHERE product_id=%s AND active", (body.product_id,)
    )
    if not product:
        raise HTTPException(status_code=404, detail="Policy source not found")
    exists = one_row(
        "SELECT 1 AS found FROM policy_snapshot WHERE product_id=%s AND source_digest=%s",
        (body.product_id, source_digest),
    )
    return {"changed": not bool(exists), "source_digest": source_digest}


@router.post("/internal/v1/policy-proposals", dependencies=[Depends(require_internal)])
def policy_proposal(body: PolicyProposalRequest) -> dict[str, Any]:
    _validate_rules(body.rules)
    if body.advertised_rate and (
        not body.rate_source_quote or body.rate_source_quote not in body.source_excerpt
    ):
        raise HTTPException(
            status_code=422, detail="The advertised rate needs a quote from the source excerpt"
        )
    source_digest = hashlib.sha256(body.source_excerpt.encode("utf-8")).hexdigest()
    with connection() as conn, conn.cursor() as cur:
        product = cur.execute(
            "SELECT product_url FROM product WHERE product_id=%s AND active FOR SHARE",
            (body.product_id,),
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Policy source not found")
        inserted = cur.execute(
            "INSERT INTO policy_snapshot (product_id,source_url,source_digest,model_provider,model_name,extraction_status,rules,source_excerpt,reviewer_note,advertised_rate,rate_source_quote) "
            "VALUES (%s,%s,%s,%s,%s,'pending_review',%s::jsonb,%s,%s,%s,%s) ON CONFLICT (product_id,source_digest) DO NOTHING RETURNING snapshot_id::text",
            (
                body.product_id,
                product["product_url"],
                source_digest,
                body.provider,
                body.model_name,
                json.dumps(body.rules),
                body.source_excerpt,
                "evidence="
                + json.dumps(body.evidence)[:1_500]
                + "; limitations="
                + json.dumps(body.limitations)[:500],
                body.advertised_rate,
                body.rate_source_quote,
            ),
        ).fetchone()
        if not inserted:
            existing = cur.execute(
                "SELECT snapshot_id::text,extraction_status FROM policy_snapshot WHERE product_id=%s AND source_digest=%s",
                (body.product_id, source_digest),
            ).fetchone()
            return {
                "status": "unchanged",
                "snapshot_id": existing["snapshot_id"],
                "review": existing["extraction_status"],
            }
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id,details) VALUES (%s,'policy_extracted','snapshot',%s,jsonb_build_object('provider',%s::text,'model',%s::text))",
            ("n8n-discovery", inserted["snapshot_id"], body.provider, body.model_name),
        )
    return {"status": "pending_review", "snapshot_id": inserted["snapshot_id"]}


@router.post("/internal/v1/save-match-batch", dependencies=[Depends(require_internal)])
def save_match_batch(body: PersistBatchRequest) -> dict[str, Any]:
    if any(row.decision not in {"eligible", "ineligible", "review"} for row in body.results):
        raise HTTPException(status_code=422, detail="Invalid decision")
    with connection() as conn, conn.cursor() as cur:
        run = cur.execute(
            "SELECT status,cursor_user_id,row_count FROM run WHERE run_id=%s FOR UPDATE",
            (body.run_id,),
        ).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run["status"] == "completed":
            return {"saved": 0, "status": "completed", "cursor_user_id": run["cursor_user_id"]}
        if run["status"] == "failed":
            raise HTTPException(
                status_code=409, detail="Retry the failed run before saving results"
            )
        cursor = run["cursor_user_id"] or ""
        if body.cursor_user_id != cursor:
            if body.last_user_id and body.last_user_id <= cursor:
                return {"saved": 0, "status": run["status"], "cursor_user_id": cursor}
            raise HTTPException(status_code=409, detail="Batch cursor is out of sequence")
        page = cur.execute(PAGE_QUERY, {"run_id": body.run_id, "cursor": cursor}).fetchone()
        if not 1 <= page["product_count"] <= MAX_DECISIONS:
            raise HTTPException(
                status_code=409, detail="A run requires between 1 and 400 pinned products"
            )
        expected = {
            (u["user_id"], p["product_id"], p["snapshot_id"])
            for u in page["users"]
            for p in u["products"]
        }
        expected.update(
            (r["user_id"], r["product_id"], r["snapshot_id"]) for r in page["preclassified_results"]
        )
        actual = {(r.user_id, r.product_id, r.snapshot_id) for r in body.results}
        if (
            actual != expected
            or len(actual) != len(body.results)
            or body.last_user_id != page["last_user_id"]
            or body.has_more != page["has_more"]
        ):
            raise HTTPException(
                status_code=422,
                detail="Batch must contain exactly the expected applicant-policy decisions",
            )
        cur.execute(
            "INSERT INTO eligibility_result (run_id,user_id,product_id,snapshot_id,decision,reasons,rule_version) "
            "SELECT %s,x.user_id,x.product_id,x.snapshot_id::uuid,x.decision,x.reasons,x.rule_version "
            "FROM jsonb_to_recordset(%s::jsonb) AS x(user_id text,product_id text,snapshot_id text,decision text,reasons jsonb,rule_version text) "
            "ON CONFLICT (run_id,user_id,product_id) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,decision=EXCLUDED.decision,reasons=EXCLUDED.reasons,rule_version=EXCLUDED.rule_version,evaluated_at=now()",
            (body.run_id, json.dumps([r.model_dump() for r in body.results])),
        )
        saved = cur.rowcount
        if saved != len(expected):
            raise HTTPException(
                status_code=409,
                detail="Batch persistence count did not match the expected decisions",
            )
        counts = cur.execute(
            "SELECT COUNT(*) AS total,COUNT(*) FILTER(WHERE decision='eligible') AS matched,"
            "COUNT(*) FILTER(WHERE decision='ineligible') AS unmatched,COUNT(*) FILTER(WHERE decision='review') AS review "
            "FROM eligibility_result WHERE run_id=%s",
            (body.run_id,),
        ).fetchone()
        state = "processing" if body.has_more else "completed"
        if not body.has_more and counts["total"] != run["row_count"] * page["product_count"]:
            raise HTTPException(
                status_code=409, detail="Run cannot complete with missing decisions"
            )
        cur.execute(
            "UPDATE run SET status=%s,cursor_user_id=%s,matched_count=%s,unmatched_count=%s,review_count=%s,"
            "started_at=COALESCE(started_at,now()),completed_at=CASE WHEN %s THEN now() ELSE NULL END,error_code=NULL,"
            "prefiltered_count=prefiltered_count+%s,candidate_count=candidate_count+%s WHERE run_id=%s",
            (
                state,
                body.last_user_id,
                counts["matched"],
                counts["unmatched"],
                counts["review"],
                not body.has_more,
                page["prefiltered_count"],
                page["candidate_count"],
                body.run_id,
            ),
        )
        cur.execute(
            "UPDATE upload SET status=%s,completed_at=CASE WHEN %s THEN now() ELSE NULL END,error_code=NULL WHERE run_id=%s",
            (state, not body.has_more, body.run_id),
        )
        if body.has_more:
            cur.execute(
                "INSERT INTO outbox (event_type,aggregate_id,payload) VALUES ('match_run',%s,jsonb_build_object('run_id',%s::text,'cursor_user_id',%s::text)) ON CONFLICT DO NOTHING",
                (body.run_id, body.run_id, body.last_user_id),
            )
        else:
            cur.execute(
                "INSERT INTO outbox (event_type,aggregate_id,payload) VALUES ('notification_preview',%s,jsonb_build_object('run_id',%s::text)) ON CONFLICT DO NOTHING",
                (body.run_id, body.run_id),
            )
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id,details) VALUES ('n8n','match_batch_saved','run',%s,jsonb_build_object('count',%s::integer))",
            (body.run_id, saved),
        )
    return {"saved": saved, "status": state, "cursor_user_id": body.last_user_id}


@router.post("/internal/v1/fail-run/{run_id}", dependencies=[Depends(require_internal)])
def fail_run(run_id: str) -> dict[str, str]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE run SET status='failed',error_code='matching_failed',completed_at=now() WHERE run_id=%s AND status!='completed'",
            (run_id,),
        )
        cur.execute(
            "UPDATE upload SET status='failed',error_code='matching_failed',completed_at=now() WHERE run_id=%s AND status!='completed'",
            (run_id,),
        )
    return {"status": "failed"}

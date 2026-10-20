import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routes import require_operator
from app.database import all_rows, one_row

router = APIRouter(dependencies=[Depends(require_operator)])

# Aggregate decisions once, including applicants whose matching has not started.
GROUPED = """
WITH pinned AS (SELECT COUNT(*) AS product_count FROM run_policy WHERE run_id=%s),
grouped AS (
 SELECT u.user_id, COUNT(e.result_id) AS processed_decisions,
 CASE WHEN COUNT(e.result_id)<pinned.product_count OR pinned.product_count=0 THEN 'processing'
      WHEN BOOL_OR(e.decision='eligible') THEN 'matched'
      WHEN BOOL_OR(e.decision='review') THEN 'review' ELSE 'unmatched' END AS status,
 COALESCE(jsonb_agg(jsonb_build_object('product_id',p.product_id,'lender',p.lender,
 'product_name',p.product_name) ORDER BY p.lender,p.product_id)
 FILTER (WHERE e.decision='eligible'),'[]'::jsonb) AS matched_products
 FROM batch_user u CROSS JOIN pinned
 LEFT JOIN eligibility_result e ON e.run_id=u.run_id AND e.user_id=u.user_id
 LEFT JOIN product p ON p.product_id=e.product_id
 WHERE u.run_id=%s GROUP BY u.user_id,pinned.product_count
)
"""


def _run(run_id: UUID | str) -> dict[str, Any]:
    row = one_row(
        "SELECT r.run_id::text,r.upload_id::text,r.status,r.row_count,r.error_code,"
        "r.created_at,r.started_at,r.completed_at,u.filename,"
        "(SELECT COUNT(*) FROM run_policy rp WHERE rp.run_id=r.run_id) AS product_count "
        "FROM run r JOIN upload u ON u.upload_id=r.upload_id WHERE r.run_id=%s",
        (str(run_id),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


@router.get("/api/runs")
def runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict[str, Any]:
    total = one_row("SELECT COUNT(*) AS total FROM run")["total"]
    items = all_rows(
        "SELECT r.run_id::text,r.status,r.row_count,r.created_at,r.error_code,u.filename "
        "FROM run r JOIN upload u ON u.upload_id=r.upload_id "
        "ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/runs/{run_id}/progress")
def progress(run_id: UUID) -> dict[str, Any]:
    row = _run(run_id)
    counts = one_row(
        GROUPED + "SELECT COALESCE(SUM(processed_decisions),0)::bigint AS processed_decisions,"
        "COUNT(*) FILTER (WHERE status!='processing') AS processed_applicants,"
        "COUNT(*) FILTER (WHERE status='matched') AS matched,"
        "COUNT(*) FILTER (WHERE status='review') AS review,"
        "COUNT(*) FILTER (WHERE status='unmatched') AS unmatched,"
        "COUNT(*) FILTER (WHERE status='processing') AS processing FROM grouped",
        (str(run_id), str(run_id)),
    )
    row.update({key: counts[key] for key in ("processed_decisions", "processed_applicants")})
    row["products"] = all_rows(
        "SELECT p.product_id,p.lender,p.product_name,p.product_url FROM run_policy rp "
        "JOIN product p ON p.product_id=rp.product_id WHERE rp.run_id=%s ORDER BY p.lender,p.product_id",
        (str(run_id),),
    )
    row["applicant_summary"] = {
        key: counts[key] for key in ("matched", "review", "unmatched", "processing")
    }
    return row


@router.get("/api/runs/{run_id}/applicants")
def applicants(
    run_id: UUID,
    status: Annotated[str | None, Query(pattern="^(matched|review|unmatched|processing)$")] = None,
    matched_product_id: Annotated[str | None, Query(max_length=100)] = None,
    q: Annotated[str, Query(max_length=128)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict[str, Any]:
    _run(run_id)
    where, params = ["TRUE"], [str(run_id), str(run_id)]
    if status:
        where.append("status=%s")
        params.append(status)
    if q:
        # Escape LIKE metacharacters so the user controls a literal prefix only.
        where.append("user_id LIKE %s ESCAPE E'\\\\'")
        params.append(q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if matched_product_id:
        where.append("matched_products @> %s::jsonb")
        params.append(json.dumps([{"product_id": matched_product_id}]))
    clause = " AND ".join(where)
    total = one_row(GROUPED + f"SELECT COUNT(*) AS total FROM grouped WHERE {clause}", params)[
        "total"
    ]
    items = all_rows(
        GROUPED + f"SELECT * FROM grouped WHERE {clause} ORDER BY user_id LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/runs/{run_id}/applicant")
def applicant(
    run_id: UUID, user_id: Annotated[str, Query(min_length=1, max_length=128)]
) -> dict[str, Any]:
    _run(run_id)
    row = one_row(
        GROUPED + "SELECT * FROM grouped WHERE user_id=%s", (str(run_id), str(run_id), user_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Applicant not found")
    row["results"] = all_rows(
        "SELECT e.result_id::text,e.user_id,e.product_id,p.lender,p.product_name,e.decision,"
        "e.reasons,e.rule_version,e.evaluated_at,e.snapshot_id::text,s.source_url,s.observed_at,s.advertised_rate "
        "FROM eligibility_result e JOIN product p ON p.product_id=e.product_id "
        "JOIN policy_snapshot s ON s.snapshot_id=e.snapshot_id WHERE e.run_id=%s AND e.user_id=%s "
        "ORDER BY p.lender,e.product_id",
        (str(run_id), user_id),
    )
    return row


@router.get("/api/runs/{run_id}/notifications")
def notifications(run_id: UUID) -> dict[str, Any]:
    _run(run_id)
    return one_row(
        "SELECT COUNT(*) FILTER (WHERE status='preview') AS preview_ready,"
        "COUNT(*) FILTER (WHERE status='sent') AS sent,"
        "COUNT(*) FILTER (WHERE status IN ('ready','claimed','sending','deferred')) AS pending,"
        "COUNT(*) FILTER (WHERE status IN ('failed','uncertain')) AS needs_attention "
        "FROM notification_delivery WHERE run_id=%s",
        (str(run_id),),
    )

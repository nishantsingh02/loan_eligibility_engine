"""Per-applicant delivery claims. Only n8n's SES node sends messages."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes import notification_preview, require_internal
from app.config import get_settings
from app.database import connection

router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_internal)])


class ClaimBatch(BaseModel):
    limit: int = Field(default=5, ge=1, le=5)


class DeliveryClaim(BaseModel):
    claim_token: UUID


class DeliveryReceipt(DeliveryClaim):
    delivery_id: UUID
    message_id: str = Field(min_length=1, max_length=200)


class DeliveryFailure(DeliveryClaim):
    # Only a definitive SES rejection can be retried automatically.
    outcome: Literal["rejected", "uncertain"] = "uncertain"
    error_code: Literal["throttled", "rejected", "transport_error"] = "transport_error"


def delivery_configuration() -> tuple[str, list[str]]:
    settings = get_settings()
    sender = settings.ses_sender.strip().lower()
    recipients = sorted(settings.test_recipient_list)
    if not sender or not recipients:
        return "", []
    for address in [sender, *recipients]:
        if address.count("@") != 1 or any(c in address for c in "\r\n,; "):
            raise HTTPException(status_code=503, detail="Invalid controlled email configuration")
    return sender, recipients


@router.post("/prepare-notifications/{run_id}")
def prepare_notifications(run_id: UUID) -> dict[str, Any]:
    summary = notification_preview(str(run_id), "workflow")
    sender, recipients = delivery_configuration()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_delivery (run_id,user_id,mode,recipient,sender,status,error_code) "
            "SELECT u.run_id,u.user_id,r.notification_mode, "
            "CASE WHEN r.notification_mode='test' AND %s::int>0 THEN "
            "(%s::text[])[1+get_byte(decode(md5(u.user_id),'hex'),0) %% %s::int] END,%s, "
            "CASE WHEN r.notification_mode='preview' THEN 'preview' "
            "WHEN %s::int=0 THEN 'deferred' ELSE 'ready' END, "
            "CASE WHEN r.notification_mode='test' AND %s::int=0 THEN 'configuration_missing' END "
            "FROM batch_user u JOIN run r ON r.run_id=u.run_id "
            "WHERE u.run_id=%s AND EXISTS (SELECT 1 FROM eligibility_result e "
            "WHERE e.run_id=u.run_id AND e.user_id=u.user_id AND e.decision='eligible') "
            "ON CONFLICT (run_id,user_id) DO NOTHING",
            (
                len(recipients),
                recipients,
                max(1, len(recipients)),
                sender or None,
                len(recipients),
                len(recipients),
                run_id,
            ),
        )
        counts = cur.execute(
            "SELECT COUNT(*)::int AS applicants,COUNT(*) FILTER (WHERE status='ready')::int AS ready "
            "FROM notification_delivery WHERE run_id=%s",
            (run_id,),
        ).fetchone()
    return {
        **summary,
        "prepared_applicants": counts["applicants"],
        "delivery_enabled": get_settings().notification_mode == "test" and counts["ready"] > 0,
    }


@router.post("/notification-claims")
def claim_notifications(body: ClaimBatch) -> dict[str, Any]:
    settings = get_settings()
    if settings.notification_mode != "test":
        return {"deliveries": []}
    sender, recipients = delivery_configuration()
    if not sender or not recipients:
        return {"deliveries": []}
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_delivery SET status='ready',claim_token=NULL,lease_until=NULL "
            "WHERE status='claimed' AND lease_until<now()"
        )
        cur.execute(
            "UPDATE notification_delivery SET status='uncertain',error_code='missing_receipt' "
            "WHERE status='sending' AND lease_until<now()"
        )
        rows = cur.execute(
            "WITH candidates AS (SELECT delivery_id FROM notification_delivery "
            "WHERE mode='test' AND status='ready' AND next_attempt_at<=now() "
            "ORDER BY created_at,delivery_id FOR UPDATE SKIP LOCKED LIMIT %s) "
            "UPDATE notification_delivery d SET status='claimed',claim_token=gen_random_uuid(), "
            "lease_until=now()+interval '5 minutes' FROM candidates c "
            "WHERE d.delivery_id=c.delivery_id RETURNING d.*",
            (body.limit,),
        ).fetchall()
        deliveries = []
        for row in rows:  # At most five bundles, never applicant CSV destinations.
            applicant = cur.execute(
                "SELECT name FROM batch_user WHERE run_id=%s AND user_id=%s",
                (row["run_id"], row["user_id"]),
            ).fetchone()
            products = cur.execute(
                "SELECT p.lender,p.product_name,s.source_url FROM eligibility_result e "
                "JOIN product p ON p.product_id=e.product_id "
                "JOIN policy_snapshot s ON s.snapshot_id=e.snapshot_id "
                "WHERE e.run_id=%s AND e.user_id=%s AND e.decision='eligible' ORDER BY p.product_id",
                (row["run_id"], row["user_id"]),
            ).fetchall()
            deliveries.append(
                {
                    "delivery_id": str(row["delivery_id"]),
                    "claim_token": str(row["claim_token"]),
                    "name": applicant["name"],
                    "user_id": row["user_id"],
                    "products": products,
                }
            )
    return {"deliveries": deliveries}


@router.post("/resume-notifications/{run_id}")
def resume_notifications(run_id: UUID) -> dict[str, int]:
    """Explicit recovery for deferred or definitively rejected work, never ambiguous sends."""
    if get_settings().notification_mode != "test":
        raise HTTPException(status_code=409, detail="Controlled delivery is disabled")
    sender, recipients = delivery_configuration()
    if not sender or not recipients:
        raise HTTPException(status_code=409, detail="Configure controlled delivery first")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_delivery SET status='ready',sender=%s, "
            "recipient=(%s::text[])[1+get_byte(decode(md5(user_id),'hex'),0) %% %s::int], "
            "claim_token=NULL,lease_until=NULL,error_code=NULL,attempt_count=0,next_attempt_at=now() "
            "WHERE run_id=%s AND mode='test' AND status IN ('deferred','failed')",
            (sender, recipients, len(recipients), run_id),
        )
        resumed = cur.rowcount
        cur.execute(
            "INSERT INTO audit_event (actor,action,entity_type,entity_id,details) "
            "VALUES ('operator','notifications_resumed','run',%s,jsonb_build_object('count',%s::int))",
            (str(run_id), resumed),
        )
    return {"resumed": resumed}


@router.post("/notification-deliveries/{delivery_id}/begin")
def begin_delivery(delivery_id: UUID, body: DeliveryClaim) -> dict[str, Any]:
    settings = get_settings()
    sender, recipients = delivery_configuration()
    with connection() as conn, conn.cursor() as cur:
        # Global lock serializes the rate gate and quota reservations across runs.
        gate = cur.execute(
            "SELECT next_send_at<=now() AS available FROM notification_send_gate "
            "WHERE singleton FOR UPDATE"
        ).fetchone()
        row = cur.execute(
            "SELECT *,lease_until>now() AS valid_lease FROM notification_delivery "
            "WHERE delivery_id=%s FOR UPDATE",
            (delivery_id,),
        ).fetchone()
        if not row or row["claim_token"] != body.claim_token:
            raise HTTPException(status_code=409, detail="Delivery claim is no longer valid")
        if row["status"] != "claimed" or not row["valid_lease"]:
            return {"delivery_enabled": False, "status": row["status"]}
        reason = None
        if settings.notification_mode != "test" or row["mode"] != "test":
            reason = "delivery_disabled"
        elif not sender or row["sender"] != sender or row["recipient"] not in recipients:
            reason = "recipient_not_allowed"
        else:
            count = cur.execute(
                "SELECT COUNT(*) FILTER (WHERE run_id=%s)::int AS run_count,"
                "COUNT(*) FILTER (WHERE send_started_at>now()-interval '24 hours')::int AS daily_count "
                "FROM notification_delivery WHERE status IN ('sending','sent','uncertain') "
                "AND (run_id=%s OR send_started_at>now()-interval '24 hours')",
                (row["run_id"], row["run_id"]),
            ).fetchone()
            if count["run_count"] >= settings.notification_run_limit:
                reason = "run_limit"
            elif count["daily_count"] >= settings.notification_daily_limit:
                reason = "daily_limit"
        if reason:
            cur.execute(
                "UPDATE notification_delivery SET status='deferred',error_code=%s WHERE delivery_id=%s",
                (reason, delivery_id),
            )
            return {"delivery_enabled": False, "status": "deferred", "reason": reason}
        if not gate["available"]:
            cur.execute(
                "UPDATE notification_delivery SET status='ready',claim_token=NULL,lease_until=NULL, "
                "next_attempt_at=now()+interval '2 seconds' WHERE delivery_id=%s",
                (delivery_id,),
            )
            return {"delivery_enabled": False, "status": "ready", "reason": "rate_limit"}
        cur.execute(
            "UPDATE notification_send_gate SET next_send_at=now()+interval '1.1 seconds' WHERE singleton"
        )
        cur.execute(
            "UPDATE notification_delivery SET status='sending',send_started_at=now(), "
            "attempt_count=attempt_count+1,lease_until=now()+interval '5 minutes' WHERE delivery_id=%s",
            (delivery_id,),
        )
    return {
        "delivery_enabled": True,
        "sender": sender,
        "recipient": row["recipient"],
        "delivery_id": str(delivery_id),
        "claim_token": str(body.claim_token),
    }


@router.post("/notification-receipt")
def record_receipt(body: DeliveryReceipt) -> dict[str, str]:
    with connection() as conn, conn.cursor() as cur:
        row = cur.execute(
            "UPDATE notification_delivery SET status='sent',provider_message_id=%s, "
            "sent_at=COALESCE(sent_at,now()),error_code=NULL WHERE delivery_id=%s AND claim_token=%s "
            "AND status IN ('sending','uncertain','sent') "
            "AND (provider_message_id IS NULL OR provider_message_id=%s) RETURNING delivery_id",
            (body.message_id, body.delivery_id, body.claim_token, body.message_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="Delivery claim or receipt conflicts")
    return {"status": "sent"}


@router.post("/notification-deliveries/{delivery_id}/failure")
def record_failure(delivery_id: UUID, body: DeliveryFailure) -> dict[str, str]:
    retry = body.outcome == "rejected" and body.error_code == "throttled"
    state = "ready" if retry else ("failed" if body.outcome == "rejected" else "uncertain")
    with connection() as conn, conn.cursor() as cur:
        row = cur.execute(
            "UPDATE notification_delivery SET status=CASE WHEN %s AND attempt_count>=3 THEN 'failed' ELSE %s END, "
            "error_code=%s,next_attempt_at=now()+interval '1 minute', "
            "claim_token=CASE WHEN %s THEN NULL ELSE claim_token END,lease_until=NULL "
            "WHERE delivery_id=%s AND claim_token=%s AND status='sending' RETURNING status",
            (retry, state, body.error_code, retry, delivery_id, body.claim_token),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="Delivery is no longer sending")
    return {"status": row["status"]}

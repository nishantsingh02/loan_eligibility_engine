import logging
import time

import httpx

from app.config import get_settings
from app.database import connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("clickpe.outbox")


def deliver_once() -> bool:
    settings = get_settings()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_heartbeat (service) VALUES ('relay') "
            "ON CONFLICT (service) DO UPDATE SET last_seen_at=now()"
        )
        event = cur.execute(
            "SELECT outbox_id,event_type,aggregate_id,payload,attempt_count FROM outbox WHERE "
            "((status IN ('pending','failed') AND next_attempt_at<=now() AND attempt_count<10) OR "
            "(status='processing' AND locked_at<now()-interval '5 minutes')) "
            "ORDER BY outbox_id FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not event:
            return False
        cur.execute(
            "UPDATE outbox SET status='processing',locked_at=now(),attempt_count=attempt_count+1 WHERE outbox_id=%s",
            (event["outbox_id"],),
        )
    payload = dict(event["payload"])
    payload.update({"event_type": event["event_type"], "outbox_id": event["outbox_id"]})
    try:
        path = {"match_run": "match-run", "notification_preview": "notification-preview"}.get(
            event["event_type"]
        )
        if not path:
            raise ValueError("unsupported_outbox_event")
        webhook_base = settings.n8n_webhook_url.rsplit("/", 1)[0]
        with httpx.Client(timeout=70, follow_redirects=False) as client:
            response = client.post(
                f"{webhook_base}/{path}",
                json=payload,
                headers={"X-Internal-Token": settings.internal_api_token},
            )
            response.raise_for_status()
    except Exception as exc:
        attempt = event["attempt_count"] + 1
        next_delay_seconds = min(3600, 30 * (2 ** min(attempt, 7)))
        status = "failed" if attempt >= 10 else "pending"
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE outbox SET status=%s,locked_at=NULL,last_error_code='workflow_unavailable',next_attempt_at=now()+(%s * interval '1 second') WHERE outbox_id=%s",
                (status, next_delay_seconds, event["outbox_id"]),
            )
            if status == "failed" and event["event_type"] == "match_run":
                cur.execute(
                    "UPDATE run SET status='failed',error_code='workflow_unavailable',completed_at=now() WHERE run_id=%s AND status!='completed'",
                    (event["aggregate_id"],),
                )
                cur.execute(
                    "UPDATE upload SET status='failed',error_code='workflow_unavailable',completed_at=now() WHERE run_id=%s",
                    (event["aggregate_id"],),
                )
        logger.warning(
            "workflow delivery failed outbox_id=%s error_type=%s retry_in_seconds=%s",
            event["outbox_id"],
            type(exc).__name__,
            next_delay_seconds,
        )
        return True
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE outbox SET status='delivered',locked_at=NULL,delivered_at=now(),last_error_code=NULL WHERE outbox_id=%s",
            (event["outbox_id"],),
        )
    return True


def main() -> None:
    settings = get_settings()
    if settings.app_env == "production" and settings.internal_api_token.startswith("change-me"):
        raise RuntimeError("Set an internal API token before starting the relay")
    while True:
        try:
            if not deliver_once():
                time.sleep(1.5)
        except Exception as exc:
            logger.error("relay loop failed error_type=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()

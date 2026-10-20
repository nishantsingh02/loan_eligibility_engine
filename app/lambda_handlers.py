import contextlib
import json
import logging
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from mangum import Mangum

from app.config import get_settings
from app.csv_ingestion import ImportErrorCode
from app.csv_ingestion import import_upload as ingest_file
from app.database import connection
from app.main import app

logger = logging.getLogger(__name__)
api = Mangum(app, lifespan="off")


def ingest_queue(event: dict, _context: object) -> dict:
    settings = get_settings()
    s3 = boto3.client("s3", region_name=settings.aws_region)
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        key = ""
        upload = None
        try:
            notification = json.loads(record["body"])
            for entry in notification.get("Records", []):
                key = unquote_plus(entry["s3"]["object"]["key"])
                upload = None
                with connection() as conn, conn.cursor() as cur:
                    upload = cur.execute(
                        "UPDATE upload SET status='processing',processing_started_at=now(),error_code=NULL "
                        "WHERE object_key=%s AND run_id IS NULL AND "
                        "(status IN ('awaiting_upload','queued') OR "
                        "(status='processing' AND COALESCE(processing_started_at,created_at) < now()-interval '10 minutes')) "
                        "RETURNING upload_id,filename",
                        (key,),
                    ).fetchone()
                    if not upload:
                        continue
                response = s3.get_object(Bucket=settings.s3_upload_bucket, Key=key)
                with tempfile.NamedTemporaryFile(
                    prefix="loan-upload-", suffix=".csv", delete=False
                ) as temporary:
                    temp_path = Path(temporary.name)
                    size = 0
                    while chunk := response["Body"].read(1024 * 1024):
                        size += len(chunk)
                        if size > settings.max_upload_bytes:
                            raise ImportErrorCode("file_too_large")
                        temporary.write(chunk)
                ingest_file(str(upload["upload_id"]), key, upload["filename"], temp_path)
                s3.delete_object(Bucket=settings.s3_upload_bucket, Key=key)
        except ImportErrorCode as exc:
            cause = exc.__cause__
            diagnostic = getattr(cause, "diag", None)
            logger.warning(
                "Upload import failed upload_id=%s error_code=%s cause_type=%s sqlstate=%s constraint=%s",
                upload.get("upload_id", "unknown") if upload else "unknown",
                exc.code,
                type(cause).__name__ if cause else "none",
                getattr(cause, "sqlstate", None) or "none",
                getattr(diagnostic, "constraint_name", None) or "none",
            )
            if upload:
                with connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE upload SET status='failed',error_code=%s,processing_started_at=NULL,completed_at=now() WHERE upload_id=%s",
                        (exc.code, upload["upload_id"]),
                    )
            with contextlib.suppress(Exception):
                s3.delete_object(Bucket=settings.s3_upload_bucket, Key=key)
        except Exception as exc:
            logger.error(
                "Upload ingest retryable failure upload_id=%s error_type=%s sqlstate=%s",
                upload.get("upload_id", "unknown") if upload else "unknown",
                type(exc).__name__,
                getattr(exc, "sqlstate", None) or "none",
            )
            if upload:
                with connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE upload SET status='queued',error_code='ingest_retryable',processing_started_at=NULL WHERE upload_id=%s",
                        (upload["upload_id"],),
                    )
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def migrate_lambda(_event: dict, _context: object) -> dict[str, str]:
    from app.migrate import migrate

    migrate()
    return {"status": "migrated"}


def purge_retained_data(_event: dict, _context: object) -> dict[str, int]:
    from app.retention import purge_old_records

    return purge_old_records()

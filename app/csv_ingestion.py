import csv
import hashlib
import io
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import get_settings
from app.database import connection

REQUIRED_COLUMNS = (
    "user_id",
    "name",
    "email",
    "monthly_income",
    "credit_score",
    "employment_status",
    "age",
)
MAX_ROWS = 100_000


class ImportErrorCode(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class ImportedRun:
    run_id: str
    rows: int
    distinct_users: int
    duplicate_emails: int


def read_csv(path: Path) -> tuple[list[tuple], str, int]:
    digest = hashlib.sha256()
    content = path.read_bytes()
    digest.update(content)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportErrorCode("invalid_encoding") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if (
        not reader.fieldnames
        or len(reader.fieldnames) != len(set(reader.fieldnames))
        or set(reader.fieldnames) != set(REQUIRED_COLUMNS)
    ):
        raise ImportErrorCode("invalid_csv_schema")
    records: list[tuple] = []
    seen_ids: set[str] = set()
    try:
        for line, row in enumerate(reader, start=2):
            if line - 1 > MAX_ROWS:
                raise ImportErrorCode("too_many_rows")
            if None in row or any(
                row.get(field) is None or not row[field].strip() for field in REQUIRED_COLUMNS
            ):
                raise ImportErrorCode("invalid_row")
            user_id = row["user_id"].strip()
            name = row["name"].strip()
            email = row["email"].strip().lower()
            status = row["employment_status"].strip()
            try:
                income = Decimal(row["monthly_income"].strip())
                credit = int(row["credit_score"].strip())
                age = int(row["age"].strip())
            except (InvalidOperation, ValueError) as exc:
                raise ImportErrorCode("invalid_row") from exc
            if (
                not user_id
                or len(user_id) > 128
                or user_id in seen_ids
                or len(name) > 200
                or len(email) > 320
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
                or not income.is_finite()
                or income < 0
                or income > 1_000_000_000
                or not 300 <= credit <= 900
                or not 18 <= age <= 100
                or status not in {"Self-Employed", "Salaried", "Business"}
            ):
                raise ImportErrorCode("invalid_row")
            seen_ids.add(user_id)
            records.append((user_id, name, email, income, credit, status, age))
    except csv.Error as exc:
        raise ImportErrorCode("invalid_csv") from exc
    if not records:
        raise ImportErrorCode("empty_csv")
    return records, digest.hexdigest(), len(records)


def import_upload(upload_id: str, object_key: str, filename: str, path: Path) -> ImportedRun:
    try:
        records, digest, row_count = read_csv(path)
    except ImportErrorCode:
        with suppress(OSError):
            path.unlink(missing_ok=True)
        raise
    run_id = str(uuid.uuid4())
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT upload_id, status, run_id FROM upload WHERE upload_id = %s FOR UPDATE",
                (upload_id,),
            )
            upload = cur.fetchone()
            if not upload:
                raise ImportErrorCode("upload_not_found")
            if upload.get("run_id"):
                existing = cur.execute(
                    "SELECT row_count, distinct_users, duplicate_email_count FROM run WHERE run_id = %s",
                    (upload["run_id"],),
                ).fetchone()
                return ImportedRun(
                    str(upload["run_id"]),
                    existing["row_count"],
                    existing["distinct_users"],
                    existing["duplicate_email_count"],
                )
            cur.execute(
                "INSERT INTO run (run_id, upload_id, status, row_count, distinct_users, cursor_user_id, notification_mode) VALUES (%s,%s,'queued',%s,%s,'',%s)",
                (run_id, upload_id, row_count, row_count, get_settings().notification_mode),
            )
            cur.executemany(
                "INSERT INTO batch_user (run_id,user_id,name,email,monthly_income,credit_score,employment_status,age) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(run_id, *record) for record in records],
            )
            cur.execute(
                "INSERT INTO run_policy (run_id,product_id,snapshot_id) "
                "SELECT %s,p.product_id,s.snapshot_id FROM product p JOIN policy_snapshot s "
                "ON s.snapshot_id=p.current_snapshot_id WHERE p.active AND s.extraction_status='approved'",
                (run_id,),
            )
            duplicate_emails = cur.execute(
                "SELECT COUNT(*) - COUNT(DISTINCT lower(email)) AS count FROM batch_user WHERE run_id=%s",
                (run_id,),
            ).fetchone()["count"]
            cur.execute(
                "UPDATE run SET duplicate_email_count=%s WHERE run_id=%s",
                (duplicate_emails, run_id),
            )
            cur.execute(
                "UPDATE upload SET content_sha256=%s, status='queued', run_id=%s, processing_started_at=NULL WHERE upload_id=%s",
                (digest, run_id, upload_id),
            )
            cur.execute(
                "INSERT INTO outbox (event_type,aggregate_id,payload) VALUES ('match_run',%s,jsonb_build_object('run_id',%s::text,'cursor_user_id','')) ON CONFLICT DO NOTHING",
                (run_id, run_id),
            )
            cur.execute(
                "INSERT INTO audit_event (actor,action,entity_type,entity_id,details) VALUES ('operator','upload_imported','run',%s,jsonb_build_object('rows',%s::integer,'sha256',%s::text))",
                (run_id, row_count, digest),
            )
        return ImportedRun(run_id, row_count, row_count, duplicate_emails)
    finally:
        with suppress(OSError):
            path.unlink(missing_ok=True)

import logging
import time
from pathlib import Path

from app.config import get_settings
from app.database import connection

logger = logging.getLogger("clickpe.retention")


def purge_old_records() -> dict[str, int]:
    retention = get_settings().retention_days
    with connection() as conn, conn.cursor() as cur:
        old_runs = cur.execute(
            "SELECT run_id FROM run WHERE created_at<now()-(%s * interval '1 day')", (retention,)
        ).fetchall()
        run_ids = [row["run_id"] for row in old_runs]
        if run_ids:
            cur.execute("DELETE FROM outbox WHERE aggregate_id=ANY(%s::uuid[])", (run_ids,))
            cur.execute("UPDATE upload SET run_id=NULL WHERE run_id=ANY(%s::uuid[])", (run_ids,))
            cur.execute("DELETE FROM run WHERE run_id=ANY(%s::uuid[])", (run_ids,))
        cur.execute(
            "DELETE FROM upload WHERE created_at<now()-(%s * interval '1 day')", (retention,)
        )
        deleted_uploads = cur.rowcount
    return {"runs_purged": len(run_ids), "uploads_purged": deleted_uploads}


def purge_abandoned_local_files() -> int:
    directory = Path(get_settings().local_upload_dir).resolve()
    if not directory.exists():
        return 0
    cutoff = time.time() - (24 * 60 * 60)
    removed = 0
    for path in directory.glob("*.csv"):
        try:
            if (
                path.is_file()
                and path.stat().st_mtime < cutoff
                and path.resolve().parent == directory
            ):
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning(
                "Could not remove stale local upload file error_type=%s", type(exc).__name__
            )
    return removed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            counts = purge_old_records()
            local_files = purge_abandoned_local_files()
            logger.info(
                "Retention sweep completed runs_purged=%s uploads_purged=%s files_purged=%s",
                counts["runs_purged"],
                counts["uploads_purged"],
                local_files,
            )
        except Exception as exc:
            logger.error("Retention sweep failed error_type=%s", type(exc).__name__)
        time.sleep(24 * 60 * 60)


if __name__ == "__main__":
    main()

from pathlib import Path

from app.database import connection


def migrate() -> None:
    directory = Path(__file__).resolve().parent.parent / "migrations"
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(742991460173)")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
            if cur.execute(
                "SELECT 1 FROM schema_migration WHERE version=%s", (path.name,)
            ).fetchone():
                continue
            cur.execute(path.read_text(encoding="utf-8"), prepare=False)
            cur.execute("INSERT INTO schema_migration (version) VALUES (%s)", (path.name,))
            print(f"Applied {path.name}")


if __name__ == "__main__":
    migrate()

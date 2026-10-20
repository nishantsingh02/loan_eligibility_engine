from pathlib import Path

import pytest

from app.csv_ingestion import ImportErrorCode, read_csv

HEADER = "user_id,name,email,monthly_income,credit_score,employment_status,age\n"


def write_csv(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sample.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_import_parser_keeps_distinct_applicants_with_a_repeated_email(tmp_path):
    path = write_csv(
        tmp_path,
        "u1,Ada,shared@example.test,45000,701,Salaried,30\n"
        "u2,Bea,shared@example.test,52000,690,Self-Employed,41\n",
    )
    rows, digest, total = read_csv(path)
    assert total == 2
    assert len(rows) == 2
    assert rows[1][0] == "u2"
    assert len(digest) == 64


@pytest.mark.parametrize(
    "body",
    [
        "u1,Ada,ada@example.test,45000,701,Salaried,30\nu1,Bea,bea@example.test,52000,690,Salaried,41\n",
        "u1,Ada,not-an-email,45000,701,Salaried,30\n",
        "u1,Ada,ada@example.test,45000,999,Salaried,30\n",
        "u1,Ada,ada@example.test,NaN,701,Salaried,30\n",
        "u1,Ada,ada@example.test,45000,701,UnknownStatus,30\n",
    ],
)
def test_parser_rejects_invalid_or_ambiguous_rows(tmp_path, body):
    with pytest.raises(ImportErrorCode):
        read_csv(write_csv(tmp_path, body))


def test_parser_rejects_missing_or_extra_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("user_id,name,email\nu1,Ada,ada@example.test\n", encoding="utf-8")
    with pytest.raises(ImportErrorCode) as error:
        read_csv(path)
    assert error.value.code == "invalid_csv_schema"


def test_parser_rejects_empty_file_without_creating_an_empty_run(tmp_path):
    with pytest.raises(ImportErrorCode) as error:
        read_csv(write_csv(tmp_path, ""))
    assert error.value.code == "empty_csv"

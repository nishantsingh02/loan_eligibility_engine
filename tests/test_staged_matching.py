"""Parity against the actual previous n8n evaluator, including ordered findings."""

import copy
import json
import os
import subprocess
from pathlib import Path

from test_pipeline import db as db
from test_pipeline import evaluate, imported_run

from app import database
from app.api.routes import NextBatchRequest, _validate_rules, next_match_batch, save_match_batch
from app.eligibility import evaluate as reference_evaluate


def test_staged_screen_preserves_decisions_and_all_findings(db, tmp_path):
    base = database.one_row(
        "SELECT rules FROM policy_snapshot WHERE product_id='axis-personal-salaried'"
    )["rules"]
    base["monthly_income"]["location_dependent"] = False
    variants = []
    for field in ("employment", "age", "monthly_income", "credit_score"):
        rules = copy.deepcopy(base)
        rules[field] = {"kind": "unknown"}
        variants.append(rules)
    rules = copy.deepcopy(base)
    rules["credit_score"] = {"kind": "required", "min": 700}
    variants.append(rules)
    for kind, minimum in (("required", 700.5), ("preferred", 700.0)):
        rules = copy.deepcopy(base)
        rules["credit_score"] = {"kind": kind, "min": minimum}
        variants.append(rules)
    rules = copy.deepcopy(base)
    rules["monthly_income"]["location_dependent"] = True
    variants.append(rules)
    with database.connection() as conn:
        for i, rules in enumerate(variants):
            _validate_rules(rules)
            product_id = f"staged-fixture-{i}"
            conn.execute(
                "INSERT INTO product (product_id,lender,product_name,product_url) VALUES (%s,'Fixture','Fixture','https://example.invalid')",
                (product_id,),
            )
            snapshot = conn.execute(
                "INSERT INTO policy_snapshot (product_id,source_url,source_digest,model_provider,model_name,extraction_status,rules,source_excerpt) "
                "VALUES (%s,'https://example.invalid',%s,'fixture','fixture','approved',%s::jsonb,'Synthetic test evidence') RETURNING snapshot_id",
                (product_id, product_id, json.dumps(rules)),
            ).fetchone()
            conn.execute(
                "UPDATE product SET current_snapshot_id=%s WHERE product_id=%s",
                (snapshot["snapshot_id"], product_id),
            )
    run = imported_run(tmp_path, 12)
    boundaries = [
        ("Salaried", 30, "50000.00", 780),
        ("Salaried", 21, "15000.00", 700),
        ("Salaried", 60, "14999.99", 699),
        ("Salaried", 20, "15000.01", 700),
        ("Salaried", 61, "50000", 650),
        ("Business", 30, "50000", 780),
        ("Self-Employed", 18, "0", 300),
        ("Salaried", 35, "25000", 649),
        ("Salaried", 35, "25001", 650),
        ("Salaried", 35, "25001", 699),
        ("Salaried", 35, "25001", 700),
        ("Business", 75, "999999", 900),
    ]
    with database.connection() as conn:
        for i, (employment, age, income, score) in enumerate(boundaries):
            conn.execute(
                "UPDATE batch_user SET employment_status=%s,age=%s,monthly_income=%s,credit_score=%s WHERE run_id=%s AND user_id=%s",
                (employment, age, income, score, run.run_id, f"user-{i:05}"),
            )
    page = next_match_batch(NextBatchRequest(run_id=run.run_id))
    assert page["prefiltered_count"] > 0
    assert page["candidate_count"] > 0
    assert page["prefiltered_count"] + page["candidate_count"] == 120
    products = database.all_rows(
        "SELECT rp.product_id,s.snapshot_id::text,s.rules FROM run_policy rp JOIN policy_snapshot s ON s.snapshot_id=rp.snapshot_id WHERE rp.run_id=%s ORDER BY rp.product_id",
        (run.run_id,),
    )
    baseline_page = {**page, "users": [{**u, "products": products} for u in page["users"]]}
    old_code = (Path(__file__).parent / "fixtures/rules-v1.js").read_text()
    output = subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('fs');const d=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(new Function('$input',d.code)({first:()=>({json:d.page})})[0].json));",
        ],
        input=json.dumps({"code": old_code, "page": baseline_page}),
        text=True,
        capture_output=True,
        check=True,
    )
    baseline = json.loads(output.stdout)["results"]
    staged = evaluate(page)

    def normalize(rows):
        return sorted(
            [{k: v for k, v in row.items() if k != "rule_version"} for row in rows],
            key=lambda r: (r["user_id"], r["product_id"]),
        )

    assert normalize([r.model_dump() for r in staged.results]) == normalize(baseline)
    for user in baseline_page["users"]:
        for product in products:
            expected = reference_evaluate(user, product["rules"])["decision"]
            assert (
                next(
                    r["decision"]
                    for r in baseline
                    if r["user_id"] == user["user_id"] and r["product_id"] == product["product_id"]
                )
                == expected
            )
    save_match_batch(staged)
    save_match_batch(staged)
    metrics = database.one_row(
        "SELECT prefiltered_count,candidate_count FROM run WHERE run_id=%s", (run.run_id,)
    )
    assert metrics["prefiltered_count"] == page["prefiltered_count"]
    assert metrics["candidate_count"] == page["candidate_count"]


def test_synthetic_benchmark_preserves_existing_catalogue(db):
    from scripts.benchmark_matching import benchmark

    report = benchmark(os.environ["TEST_DATABASE_URL"], applicants=40, products=5)
    assert report["evaluated_pairs"] == report["expected_pairs"] == 200
    assert report["parity"] is True
    assert report["candidate_pairs"] + report["sql_prefiltered_pairs"] == 200
    assert report["max_decisions_per_batch"] <= 400
    assert report["persisted_results"] is False
    assert database.one_row("SELECT COUNT(*) AS n FROM product WHERE active")["n"] == 2
    assert database.one_row("SELECT COUNT(*) AS n FROM eligibility_result")["n"] == 0

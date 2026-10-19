from decimal import Decimal, InvalidOperation
from typing import Any

ENGINE_VERSION = "rules-v1"


def evaluate(profile: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Evaluate only documented criteria. A soft preference or missing term yields review."""
    findings: list[dict[str, Any]] = []
    hard_failure = False
    unresolved = False

    employment = rules.get("employment", {})
    if employment.get("kind") == "required":
        passed = profile.get("employment_status") == employment.get("status")
        findings.append(
            {
                "criterion": "employment_status",
                "status": "pass" if passed else "fail",
                "required_value": employment.get("status"),
                "observed_value": profile.get("employment_status"),
            }
        )
        hard_failure |= not passed
    else:
        unresolved = True
        findings.append(
            {
                "criterion": "employment_status",
                "status": "unknown",
                "message": "This product's employment criteria need confirmation.",
            }
        )

    age = rules.get("age", {})
    if age.get("kind") == "unknown" or (
        age.get("kind") != "required" and not age.get("min") and not age.get("max")
    ):
        unresolved = True
        findings.append(
            {
                "criterion": "age",
                "status": "unknown",
                "message": "The available source does not establish the age criteria.",
            }
        )
    elif age.get("kind") == "required":
        value = profile.get("age")
        passed = isinstance(value, int) and age.get("min", value) <= value <= age.get("max", value)
        findings.append(
            {
                "criterion": "age",
                "status": "pass" if passed else "fail",
                "minimum": age.get("min"),
                "maximum": age.get("max"),
                "observed_value": value,
            }
        )
        hard_failure |= not passed

    income = rules.get("monthly_income", {})
    if income.get("kind") == "unknown" or income.get("location_dependent"):
        unresolved = True
        findings.append(
            {
                "criterion": "monthly_income",
                "status": "unknown",
                "message": "The source says the income threshold depends on location, which is not in this dataset.",
            }
        )
    elif income.get("kind") == "required":
        try:
            value = Decimal(str(profile.get("monthly_income")))
            passed = value >= Decimal(str(income["min"]))
        except (InvalidOperation, KeyError, TypeError):
            value, passed = None, False
            unresolved = True
        findings.append(
            {
                "criterion": "monthly_income",
                "status": "pass" if passed else ("unknown" if value is None else "fail"),
                "minimum": income.get("min"),
                "currency": income.get("currency", "INR"),
            }
        )
        hard_failure |= value is not None and not passed

    score = rules.get("credit_score", {})
    if score.get("kind") == "required":
        value = profile.get("credit_score")
        passed = isinstance(value, int) and value >= score.get("min", value)
        findings.append(
            {
                "criterion": "credit_score",
                "status": "pass" if passed else "fail",
                "minimum": score.get("min"),
                "observed_value": value,
            }
        )
        hard_failure |= not passed
    elif score.get("kind") == "preferred":
        value = profile.get("credit_score")
        passed = isinstance(value, int) and value >= score.get("min", value)
        findings.append(
            {
                "criterion": "credit_score",
                "status": "pass" if passed else "review",
                "preferred_minimum": score.get("min"),
                "observed_value": value,
                "message": "The source describes this score as preferred, not a hard eligibility cutoff.",
            }
        )
        unresolved |= not passed
    else:
        unresolved = True
        findings.append(
            {
                "criterion": "credit_score",
                "status": "unknown",
                "message": "The available source does not establish a credit-score rule.",
            }
        )

    decision = "ineligible" if hard_failure else ("review" if unresolved else "eligible")
    return {"decision": decision, "reasons": findings, "rule_version": ENGINE_VERSION}

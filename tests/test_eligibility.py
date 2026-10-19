from app.eligibility import evaluate

AXIS = {
    "employment": {"status": "Salaried", "kind": "required"},
    "age": {"min": 21, "max": 60, "kind": "required"},
    "monthly_income": {"min": 15_000, "kind": "required", "currency": "INR"},
    "credit_score": {"min": 700, "kind": "preferred"},
}
BJAJ = {
    "employment": {"status": "Salaried", "kind": "required"},
    "monthly_income": {
        "min": 25_001,
        "kind": "required",
        "currency": "INR",
        "location_dependent": True,
    },
    "credit_score": {"min": 650, "kind": "required"},
    "age": {"kind": "unknown"},
}


def profile(**overrides):
    return {
        "employment_status": "Salaried",
        "age": 35,
        "monthly_income": 80_000,
        "credit_score": 750,
        **overrides,
    }


def test_all_confirmed_axis_criteria_produce_an_explainable_match():
    result = evaluate(profile(), AXIS)
    assert result["decision"] == "eligible"
    assert {item["criterion"] for item in result["reasons"]} == {
        "employment_status",
        "age",
        "monthly_income",
        "credit_score",
    }


def test_preferred_credit_score_does_not_become_a_hard_rejection():
    result = evaluate(profile(credit_score=675), AXIS)
    assert result["decision"] == "review"
    assert (
        next(item for item in result["reasons"] if item["criterion"] == "credit_score")["status"]
        == "review"
    )


def test_employment_and_age_failures_are_explicit_hard_rejections():
    assert evaluate(profile(employment_status="Business"), AXIS)["decision"] == "ineligible"
    age_result = evaluate(profile(age=20), AXIS)
    assert age_result["decision"] == "ineligible"
    assert (
        next(item for item in age_result["reasons"] if item["criterion"] == "age")["status"]
        == "fail"
    )


def test_unavailable_city_specific_rule_keeps_bajaj_in_review():
    result = evaluate(profile(), BJAJ)
    assert result["decision"] == "review"
    assert {item["criterion"] for item in result["reasons"] if item["status"] == "unknown"} == {
        "age",
        "monthly_income",
    }


def test_firm_credit_cutoff_can_fail_while_other_policy_terms_need_review():
    result = evaluate(profile(credit_score=620), BJAJ)
    assert result["decision"] == "ineligible"
    assert (
        next(item for item in result["reasons"] if item["criterion"] == "credit_score")["status"]
        == "fail"
    )

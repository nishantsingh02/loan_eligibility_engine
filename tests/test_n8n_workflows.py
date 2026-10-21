import json
import subprocess
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / "n8n" / "workflows"


def discovery_codes():
    workflow = json.loads(
        (WORKFLOW_DIR / "01-discover-official-policies.json").read_text(encoding="utf-8")
    )
    nodes = workflow["nodes"]

    def code(prefix):
        node = next(item for item in nodes if item["name"].startswith(prefix))
        return node["parameters"].get("jsCode", "")

    return {
        "build": code("Build strict extraction request"),
        "validate_primary": code("Validate primary extraction"),
        "prepare_gemini": code("Prepare Gemini fallback"),
        "validate_gemini": code("Validate Gemini extraction"),
    }


def test_discovery_uses_free_structured_model_and_records_requested_model():
    codes = discovery_codes()
    script = """
const {codes}=JSON.parse(require('fs').readFileSync(0,'utf8'));
const source={product_id:'fixture',lender:'Fixture lender',product_name:'Fixture loan',
 product_url:'https://example.com',source_excerpt:'Official fixture terms for extraction.'};
const build=new Function('$',codes.build);
const base=build(()=>({item:{json:source}})).json;
const request=JSON.parse(JSON.stringify(base.request));
base.request.model='fixture-selected-model';
const parsed={employment:{kind:'unknown'},age:{kind:'unknown'},
 monthly_income:{kind:'unknown'},credit_score:{kind:'unknown'}};
const validate=new Function('$json','$',codes.validate_primary);
const result=validate({choices:[{message:{content:JSON.stringify(parsed)}}]},
 ()=>({item:{json:base}})).json;
console.log(JSON.stringify({request,model_name:result.model_name}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"codes": codes}),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    request = data["request"]
    assert request["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["provider"]["require_parameters"] is True
    assert request["max_tokens"] >= 4000
    assert request["reasoning"] == {"effort": "low", "exclude": True}
    assert data["model_name"] == "fixture-selected-model"


def test_discovery_provider_retries_are_bounded():
    workflow = json.loads(
        (WORKFLOW_DIR / "01-discover-official-policies.json").read_text(encoding="utf-8")
    )
    providers = [node for node in workflow["nodes"] if node["name"].startswith("Extract with")]
    assert len(providers) == 2
    for node in providers:
        assert node["retryOnFail"] is True
        assert 2 <= node["maxTries"] <= 3
        assert node["waitBetweenTries"] >= 1000
        assert node["parameters"]["options"]["timeout"] <= 60_000


def test_discovery_rates_and_quotes_are_literal_source_choices():
    codes = discovery_codes()
    script = """
const {codes}=JSON.parse(require('fs').readFileSync(0,'utf8'));
const source={product_id:'fixture',lender:'Fixture lender',product_name:'Fixture loan',
 product_url:'https://example.com',source_excerpt:
 'Personal loan interest rate starts at 10% p.a. to 30.50% p.a.\\nProcessing fee up to 4.13% payable at disbursement.\\nInterest rate is 10–30.50% p.a.\\nInterest rate between 10% and 30.50% p.a.\\nMinimum age 21 years.'};
const base=new Function('$',codes.build)(
 ()=>({item:{json:source}})).json;
const rate='starts at 10% p.a. to 30.50% p.a.';
const parsed={employment:{kind:'unknown'},age:{kind:'unknown'},
 monthly_income:{kind:'unknown'},credit_score:{kind:'unknown'},
 advertised_rate:rate,rate_source_quote:'A paraphrased quote that is not in the source'};
const validate=new Function('$json','$',codes.validate_primary);
const result=validate({choices:[{message:{content:JSON.stringify(parsed)}}]},
 ()=>({item:{json:base}})).json;
let rejected=false;
try {validate({choices:[{message:{content:JSON.stringify({...parsed,advertised_rate:'10% to 30.5% p.a.'})}}]},
 ()=>({item:{json:base}}));}catch{rejected=true;}
const fallback=new Function('$',codes.prepare_gemini)(
 ()=>({item:{json:base}})).json;
const google=new Function('$json','$',codes.validate_gemini)(
 {candidates:[{content:{parts:[{text:JSON.stringify(parsed)}]}}]},
 ()=>({item:{json:fallback}})).json;
console.log(JSON.stringify({schema:base.request.response_format.json_schema.schema,
 rate_quotes:base.rate_quotes,
 google_schema:fallback.request.generationConfig.responseSchema,result,google,rejected}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"codes": codes}),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    rate = "starts at 10% p.a. to 30.50% p.a."
    for schema in (data["schema"], data["google_schema"]):
        assert rate in schema["properties"]["advertised_rate"]["enum"]
        assert "4.13%" not in schema["properties"]["advertised_rate"]["enum"]
        assert "10% to 30.5% p.a." not in schema["properties"]["advertised_rate"]["enum"]
        assert "10–30.50% p.a." in schema["properties"]["advertised_rate"]["enum"]
        assert "between 10% and 30.50% p.a." in schema["properties"]["advertised_rate"]["enum"]
    assert not any("Processing fee" in quote for quote in data["rate_quotes"])
    assert not any(
        isinstance(value, str) and "4.13% pa" in value
        for value in data["schema"]["properties"]["advertised_rate"]["enum"]
    )
    for policy in (data["result"], data["google"]):
        assert policy["advertised_rate"] == rate
        assert policy["rate_source_quote"] == (
            "Personal loan interest rate starts at 10% p.a. to 30.50% p.a."
        )
    assert data["rejected"] is True


def test_discovery_only_promotes_explicit_credit_score_thresholds():
    codes = discovery_codes()
    script = """
const {codes}=JSON.parse(require('fs').readFileSync(0,'utf8'));
const build=new Function('$',codes.build);
const validate=new Function('$json','$',codes.validate_primary);
const prepareGemini=new Function('$',codes.prepare_gemini);
const validateGemini=new Function('$json','$',codes.validate_gemini);
function run(source_excerpt, modelScore={kind:'unknown'}) {
  const source={product_id:'fixture',lender:'Fixture lender',product_name:'Fixture loan',
    product_url:'https://example.com',source_excerpt};
  const base=build(()=>({item:{json:source}})).json;
  const parsed={employment:{kind:'unknown'},age:{kind:'unknown'},
    monthly_income:{kind:'unknown'},credit_score:modelScore};
  const primary=validate({choices:[{message:{content:JSON.stringify(parsed)}}]},
    ()=>({item:{json:base}})).json;
  const fallback=prepareGemini(()=>({item:{json:base}})).json;
  const google=validateGemini({candidates:[{content:{parts:[{text:JSON.stringify(parsed)}]}}]},
    ()=>({item:{json:fallback}})).json;
  return {primary,google};
}
console.log(JSON.stringify({
  required:run('Eligibility. CIBIL Score: 650 and above required.'),
  inclusive_before:run('Eligibility. Minimum CIBIL score at or above 650.'),
  preferred:run('Eligibility. Credit score of 700 is preferred.',{kind:'required',min:640}),
  preferred_before_number:run('Eligibility. Recommended minimum credit score: 700.',{kind:'required',min:640}),
  negated:run('Eligibility. CIBIL Score is not required.'),
  negated_before_number:run('Eligibility. No minimum CIBIL score of 650 is required.',{kind:'required',min:640}),
  strict_greater:run('Eligibility. CIBIL score > 650 is required.',{kind:'required',min:640}),
  upper_bound:run('Eligibility. Credit score below 650 is required.',{kind:'required',min:640}),
  upper_bound_after_number:run('Eligibility. CIBIL score 650 or below is required.',{kind:'required',min:640}),
  unicode_lower_bound:run('Eligibility. Credit score '+String.fromCharCode(0x2265)+' 650 is required.'),
  unicode_upper_bound:run('Eligibility. Credit score '+String.fromCharCode(0x2264)+' 650 is required.',{kind:'required',min:640}),
  multiple_thresholds:run('Eligibility. Minimum credit score 650 required, 750 preferred.',{kind:'required',min:640}),
  ambiguous:run('Eligibility. Credit score is considered with the application.')
}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"codes": codes}),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    for provider in ("primary", "google"):
        assert data["required"][provider]["rules"]["credit_score"] == {
            "kind": "required",
            "min": 650,
        }
        assert data["required"][provider]["evidence"] == [
            {"criterion": "credit_score", "source_quote": "CIBIL Score: 650 and above required"}
        ]
        assert data["inclusive_before"][provider]["rules"]["credit_score"] == {
            "kind": "required",
            "min": 650,
        }
        assert data["unicode_lower_bound"][provider]["rules"]["credit_score"] == {
            "kind": "required",
            "min": 650,
        }
        assert data["preferred"][provider]["rules"]["credit_score"] == {
            "kind": "preferred",
            "min": 700,
        }
        assert data["preferred"][provider]["evidence"] == [
            {"criterion": "credit_score", "source_quote": "Credit score of 700 is preferred"}
        ]
        assert data["preferred_before_number"][provider]["rules"]["credit_score"] == {
            "kind": "preferred",
            "min": 700,
        }
        assert data["preferred_before_number"][provider]["evidence"] == [
            {"criterion": "credit_score", "source_quote": "Recommended minimum credit score: 700"}
        ]
        for name in ("negated", "ambiguous"):
            assert data[name][provider]["rules"]["credit_score"] == {"kind": "unknown"}
            assert data[name][provider]["evidence"] == []
        for name, quote in (
            ("negated_before_number", "No minimum CIBIL score of 650 is required"),
            ("strict_greater", "CIBIL score > 650 is required"),
            ("upper_bound", "Credit score below 650 is required"),
            ("upper_bound_after_number", "CIBIL score 650 or below is required"),
            ("unicode_upper_bound", "Credit score \u2264 650 is required"),
            ("multiple_thresholds", "Minimum credit score 650 required, 750 preferred"),
        ):
            result = data[name][provider]
            assert result["rules"]["credit_score"] == {"kind": "unknown"}
            assert result["evidence"] == [{"criterion": "credit_score", "source_quote": quote}]
            assert result["limitations"]


def test_workflow_exports_are_valid_and_have_no_embedded_credentials():
    workflows = list(WORKFLOW_DIR.glob("*.json"))
    assert len(workflows) == 3
    for path in workflows:
        workflow = json.loads(path.read_text(encoding="utf-8"))
        assert workflow["active"] is False
        for node in workflow["nodes"]:
            for credential in node.get("credentials", {}).values():
                assert "password" not in credential
                assert "api_key" not in credential


def test_matcher_fetches_bounded_batches_without_applicant_names_or_emails():
    workflow = json.loads((WORKFLOW_DIR / "02-match-applicants.json").read_text(encoding="utf-8"))
    postgres = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.postgres")
    matcher = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.code")
    query = postgres["parameters"]["query"].lower()
    assert "400/greatest(count(*)" in query
    assert "run_policy" in query
    from app.matching import PAGE_QUERY

    assert query == PAGE_QUERY.replace("%(run_id)s", "$1").replace("%(cursor)s", "$2").lower()
    assert "monthly_income" in query and "credit_score" in query
    assert "u.name" not in query and "u.email" not in query
    assert "user.products" in matcher["parameters"]["jsCode"]
    assert "maxTries" not in postgres["parameters"]


def test_notification_workflow_gates_ses_and_does_not_retry_sends():
    workflow = json.loads(
        (WORKFLOW_DIR / "03-prepare-notification-preview.json").read_text(encoding="utf-8")
    )
    nodes = workflow["nodes"]
    ses = next(n for n in nodes if n["type"] == "n8n-nodes-base.awsSes")
    assert ses["retryOnFail"] is False
    assert ses["parameters"]["toAddresses"] == ["={{ $json.recipient }}"]
    assert any(n["type"] == "n8n-nodes-base.if" for n in nodes)
    gate = workflow["connections"]["Controlled test delivery enabled?"]["main"]
    assert gate[0][0]["node"] == "Attach personalized message"
    assert (
        workflow["connections"]["Attach personalized message"]["main"][0][0]["node"] == ses["name"]
    )
    assert gate[1][0]["node"] == "Deliver applicants sequentially"
    assert ses["parameters"]["authentication"] == "assumeRole"
    assert any(n["type"] == "n8n-nodes-base.scheduleTrigger" for n in nodes)
    assert any(n["name"] == "Reserve delivery before SES" for n in nodes)
    assert next(n for n in nodes if n["name"] == "Record SES receipt")["retryOnFail"] is True


def test_ses_generic_error_classification_preserves_ambiguous_sends():
    workflow = json.loads(
        (WORKFLOW_DIR / "03-prepare-notification-preview.json").read_text(encoding="utf-8")
    )
    code = next(
        n["parameters"]["jsCode"] for n in workflow["nodes"] if n["name"] == "Classify send failure"
    )
    cases = {
        "Bad request - please check your parameters": ("rejected", "rejected"),
        "Forbidden - perhaps check your credentials?": ("rejected", "rejected"),
        "The service is receiving too many requests from you": ("rejected", "throttled"),
        "socket hang up": ("uncertain", "transport_error"),
    }
    result = subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('fs');const {code,cases}=JSON.parse(fs.readFileSync(0,'utf8'));const f=new Function('$json','$',code);console.log(JSON.stringify(Object.keys(cases).map(error=>f({error},()=>({item:{json:{delivery_id:'fixture',claim_token:'fixture'}}})).json)));",
        ],
        input=json.dumps({"code": code, "cases": cases}),
        text=True,
        capture_output=True,
        check=True,
    )
    for row, expected in zip(json.loads(result.stdout), cases.values(), strict=True):
        assert (row["outcome"], row["error_code"]) == expected

ALTER TABLE run ADD COLUMN prefiltered_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run ADD COLUMN candidate_count INTEGER NOT NULL DEFAULT 0;

-- Cheap structured checks in the n8n Postgres stage. Complete findings keep
-- rejected pairs auditable without running them through the JavaScript stage.
CREATE FUNCTION policy_hard_screen(profile JSONB, rules JSONB) RETURNS JSONB
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
  findings JSONB := '[]'::jsonb;
  rule JSONB;
  passed BOOLEAN;
  hard_failure BOOLEAN := FALSE;
BEGIN
  rule := rules->'employment';
  IF rule->>'kind'='required' THEN
    passed := profile->>'employment_status'=rule->>'status';
    findings := findings || jsonb_build_array(jsonb_build_object('criterion','employment_status','status',CASE WHEN passed THEN 'pass' ELSE 'fail' END,'required_value',rule->'status','observed_value',profile->'employment_status'));
    hard_failure := hard_failure OR NOT passed;
  ELSE
    findings := findings || '[{"criterion":"employment_status","status":"unknown","message":"This product employment rule needs confirmation."}]'::jsonb;
  END IF;
  rule := rules->'age';
  IF rule->>'kind'='required' THEN
    passed := (profile->>'age')::int BETWEEN (rule->>'min')::int AND (rule->>'max')::int;
    findings := findings || jsonb_build_array(jsonb_build_object('criterion','age','status',CASE WHEN passed THEN 'pass' ELSE 'fail' END,'minimum',rule->'min','maximum',rule->'max','observed_value',profile->'age'));
    hard_failure := hard_failure OR NOT passed;
  ELSE
    findings := findings || '[{"criterion":"age","status":"unknown","message":"The official source does not establish the age criteria."}]'::jsonb;
  END IF;
  rule := rules->'monthly_income';
  IF rule->>'kind'='required' AND NOT COALESCE((rule->>'location_dependent')::boolean,FALSE) THEN
    passed := (profile->>'monthly_income')::numeric >= (rule->>'min')::numeric;
    findings := findings || jsonb_build_array(jsonb_build_object('criterion','monthly_income','status',CASE WHEN passed THEN 'pass' ELSE 'fail' END,'minimum',rule->'min','currency',COALESCE(rule->>'currency','INR'),'observed_value',profile->'monthly_income'));
    hard_failure := hard_failure OR NOT passed;
  ELSE
    findings := findings || '[{"criterion":"monthly_income","status":"unknown","message":"Location-specific income terms cannot be checked from this file."}]'::jsonb;
  END IF;
  rule := rules->'credit_score';
  IF rule->>'kind'='required' THEN
    passed := (profile->>'credit_score')::int >= (rule->>'min')::numeric;
    findings := findings || jsonb_build_array(jsonb_build_object('criterion','credit_score','status',CASE WHEN passed THEN 'pass' ELSE 'fail' END,'minimum',rule->'min','observed_value',profile->'credit_score'));
    hard_failure := hard_failure OR NOT passed;
  ELSIF rule->>'kind'='preferred' THEN
    passed := (profile->>'credit_score')::int >= (rule->>'min')::numeric;
    findings := findings || jsonb_build_array(jsonb_build_object('criterion','credit_score','status',CASE WHEN passed THEN 'pass' ELSE 'review' END,'preferred_minimum',rule->'min','observed_value',profile->'credit_score','message','The source describes this as preferred, not a hard cutoff.'));
  ELSE
    findings := findings || '[{"criterion":"credit_score","status":"unknown","message":"The official source does not establish a score cutoff."}]'::jsonb;
  END IF;
  RETURN jsonb_build_object('hard_failure',hard_failure,'findings',findings);
END
$$;

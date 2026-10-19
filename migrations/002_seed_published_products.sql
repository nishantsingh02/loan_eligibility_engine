INSERT INTO product (product_id, lender, product_name, product_url)
VALUES
  ('axis-personal-salaried', 'Axis Bank', 'Personal Loan for Salaried', 'https://www.axis.bank.in/loans/personal-loan/personal-loan-for-salaried'),
  ('bajaj-personal-loan', 'Bajaj Finance', 'Personal Loan', 'https://www.bajajfinserv.in/personal-loan')
ON CONFLICT (product_id) DO NOTHING;

-- Curated baseline. Preferred thresholds stay advisory; city-dependent or undocumented
-- eligibility fields force a review result instead of an invented pass/fail decision.
INSERT INTO policy_snapshot
  (product_id, source_url, source_digest, effective_date, model_provider, model_name,
   extraction_status, rules, source_excerpt, reviewer_note)
VALUES
  ('axis-personal-salaried', 'https://www.axis.bank.in/loans/personal-loan/personal-loan-for-salaried',
   'curated-2026-10-14-axis-personal-salaried-v1', '2026-10-14', 'curated', 'manual-source-review', 'approved',
   '{"version":"1","employment":{"status":"Salaried","kind":"required"},"age":{"min":21,"max":60,"kind":"required"},"monthly_income":{"min":15000,"kind":"required","currency":"INR"},"credit_score":{"min":700,"kind":"preferred"},"incomplete_eligibility":"review"}'::jsonb,
   'Official Axis Bank salaried personal-loan page: https://www.axis.bank.in/loans/personal-loan/personal-loan-for-salaried ; eligibility page lists age 21-60, monthly income starting at INR 15,000, and a credit score of 700 as preferred. Source checked 2026-10-14.',
   'Manually curated prototype baseline from the linked official page. Preferred score is never used as a hard rejection; omitted terms trigger review.'),
  ('bajaj-personal-loan', 'https://www.bajajfinserv.in/personal-loan',
   'curated-2026-10-14-bajaj-personal-loan-v1', '2026-10-14', 'curated', 'manual-source-review', 'approved',
   '{"version":"1","employment":{"kind":"unknown"},"monthly_income":{"min":25001,"kind":"required","currency":"INR","location_dependent":true},"credit_score":{"min":650,"kind":"required"},"age":{"kind":"unknown"},"incomplete_eligibility":"review"}'::jsonb,
   'Official Bajaj Finserv personal-loan page: https://www.bajajfinserv.in/personal-loan ; page checked 2026-10-14. Credit score 650 and monthly salary from INR 25,001 are listed; salary criteria vary by city.',
   'Manually curated prototype baseline from the linked official page. City, age, and employment eligibility are not fully represented by the supplied CSV; qualifying profiles stay in review.')
ON CONFLICT (product_id, source_digest) DO NOTHING;

UPDATE product p SET current_snapshot_id = s.snapshot_id
FROM policy_snapshot s
WHERE p.product_id = s.product_id AND s.source_digest LIKE 'curated-2026-10-14-%'
  AND p.current_snapshot_id IS NULL;

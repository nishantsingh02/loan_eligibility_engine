"""Bounded staged matching contract shared with n8n's Postgres node."""

MAX_DECISIONS = 400
ENGINE_VERSION = "rules-v2-staged"

PAGE_QUERY = """
WITH started AS (
  UPDATE run SET status='processing',started_at=COALESCE(started_at,now())
  WHERE run_id=%(run_id)s AND status='queued' RETURNING run_id
), policies AS MATERIALIZED (
  SELECT rp.product_id,s.snapshot_id::text,s.rules FROM run_policy rp
  JOIN policy_snapshot s ON s.snapshot_id=rp.snapshot_id WHERE rp.run_id=%(run_id)s
), bounds AS (
  SELECT LEAST(200,GREATEST(1,400/GREATEST(COUNT(*),1)))::int AS page_size,
         COUNT(*) AS product_count FROM policies
), page AS MATERIALIZED (
  SELECT user_id,monthly_income,credit_score,employment_status,age
  FROM batch_user WHERE run_id=%(run_id)s AND user_id>%(cursor)s
  ORDER BY user_id LIMIT (SELECT page_size+1 FROM bounds)
), numbered AS (
  SELECT *,ROW_NUMBER() OVER (ORDER BY user_id) AS seq FROM page
), selected AS MATERIALIZED (
  SELECT * FROM numbered WHERE seq<=(SELECT page_size FROM bounds)
), pairs AS MATERIALIZED (
  SELECT u.user_id,p.product_id,p.snapshot_id,p.rules,
         policy_hard_screen(to_jsonb(u)-'seq',p.rules) AS screen
  FROM selected u CROSS JOIN policies p
)
SELECT %(run_id)s::text AS run_id,%(cursor)s::text AS cursor_user_id,
  (SELECT product_count FROM bounds) AS product_count,
  COALESCE((SELECT jsonb_agg(to_jsonb(u)-'seq' || jsonb_build_object('products',
    (SELECT COALESCE(jsonb_agg(jsonb_build_object('product_id',p.product_id,
      'snapshot_id',p.snapshot_id,'rules',p.rules,'mandatory_findings',p.screen->'findings') ORDER BY p.product_id),'[]'::jsonb)
     FROM pairs p WHERE p.user_id=u.user_id AND NOT (p.screen->>'hard_failure')::boolean))
    ORDER BY u.user_id) FROM selected u),'[]'::jsonb) AS users,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',user_id,'product_id',product_id,
    'snapshot_id',snapshot_id,'decision','ineligible','reasons',screen->'findings',
    'rule_version','rules-v2-staged') ORDER BY user_id,product_id)
    FROM pairs WHERE (screen->>'hard_failure')::boolean),'[]'::jsonb) AS preclassified_results,
  (SELECT COUNT(*) FROM pairs WHERE (screen->>'hard_failure')::boolean) AS prefiltered_count,
  (SELECT COUNT(*) FROM pairs WHERE NOT (screen->>'hard_failure')::boolean) AS candidate_count,
  (SELECT COUNT(*) FROM numbered)>(SELECT page_size FROM bounds) AS has_more,
  COALESCE((SELECT MAX(user_id) FROM selected),%(cursor)s::text) AS last_user_id
"""

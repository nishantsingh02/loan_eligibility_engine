CREATE TABLE run_policy (
  run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES product(product_id),
  snapshot_id UUID NOT NULL REFERENCES policy_snapshot(snapshot_id),
  PRIMARY KEY (run_id, product_id)
);

-- Preserve policies already used, then pin untouched products for existing runs.
INSERT INTO run_policy (run_id,product_id,snapshot_id)
SELECT DISTINCT ON (run_id,product_id) run_id,product_id,snapshot_id
FROM eligibility_result ORDER BY run_id,product_id,evaluated_at
ON CONFLICT DO NOTHING;
INSERT INTO run_policy (run_id,product_id,snapshot_id)
SELECT r.run_id,p.product_id,p.current_snapshot_id FROM run r
CROSS JOIN product p JOIN policy_snapshot s ON s.snapshot_id=p.current_snapshot_id
WHERE r.status!='completed' AND p.active AND s.extraction_status='approved'
ON CONFLICT DO NOTHING;

ALTER TABLE policy_snapshot ADD COLUMN advertised_rate TEXT;
ALTER TABLE policy_snapshot ADD COLUMN rate_source_quote TEXT;
CREATE TABLE service_heartbeat (
  service TEXT PRIMARY KEY,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE test_delivery (
  delivery_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL UNIQUE REFERENCES run(run_id) ON DELETE CASCADE,
  recipient TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('claimed','sent','uncertain')),
  provider_message_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at TIMESTAMPTZ
);

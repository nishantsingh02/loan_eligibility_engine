CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS product (
  product_id TEXT PRIMARY KEY,
  lender TEXT NOT NULL,
  product_name TEXT NOT NULL,
  product_url TEXT NOT NULL,
  current_snapshot_id UUID,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_snapshot (
  snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id TEXT NOT NULL REFERENCES product(product_id),
  source_url TEXT NOT NULL,
  source_digest TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_date DATE,
  model_provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  extraction_status TEXT NOT NULL CHECK (extraction_status IN ('pending_review','approved','rejected')),
  rules JSONB NOT NULL,
  source_excerpt TEXT NOT NULL,
  reviewer_note TEXT,
  UNIQUE (product_id, source_digest)
);
ALTER TABLE product ADD CONSTRAINT product_current_snapshot_fk FOREIGN KEY (current_snapshot_id) REFERENCES policy_snapshot(snapshot_id);

CREATE TABLE IF NOT EXISTS upload (
  upload_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  object_key TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  content_sha256 TEXT,
  status TEXT NOT NULL CHECK (status IN ('awaiting_upload','queued','processing','completed','failed')),
  run_id UUID,
  error_code TEXT,
  processing_started_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS run (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id UUID NOT NULL UNIQUE REFERENCES upload(upload_id),
  status TEXT NOT NULL CHECK (status IN ('queued','processing','completed','failed')),
  row_count INTEGER NOT NULL DEFAULT 0,
  distinct_users INTEGER NOT NULL DEFAULT 0,
  duplicate_email_count INTEGER NOT NULL DEFAULT 0,
  matched_count INTEGER NOT NULL DEFAULT 0,
  unmatched_count INTEGER NOT NULL DEFAULT 0,
  review_count INTEGER NOT NULL DEFAULT 0,
  cursor_user_id TEXT,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);
ALTER TABLE upload ADD CONSTRAINT upload_run_fk FOREIGN KEY (run_id) REFERENCES run(run_id);

CREATE TABLE IF NOT EXISTS batch_user (
  run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  monthly_income NUMERIC(12,2) NOT NULL,
  credit_score INTEGER NOT NULL,
  employment_status TEXT NOT NULL,
  age INTEGER NOT NULL,
  PRIMARY KEY (run_id, user_id)
);
CREATE INDEX IF NOT EXISTS batch_user_page_idx ON batch_user(run_id, user_id);

CREATE TABLE IF NOT EXISTS eligibility_result (
  result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  product_id TEXT NOT NULL REFERENCES product(product_id),
  snapshot_id UUID NOT NULL REFERENCES policy_snapshot(snapshot_id),
  decision TEXT NOT NULL CHECK (decision IN ('eligible','ineligible','review')),
  reasons JSONB NOT NULL,
  rule_version TEXT NOT NULL,
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, user_id, product_id)
);
CREATE INDEX IF NOT EXISTS eligibility_result_filter_idx ON eligibility_result(run_id, decision, product_id);

CREATE TABLE IF NOT EXISTS outbox (
  outbox_id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  aggregate_id UUID NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','delivered','failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  locked_at TIMESTAMPTZ,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  delivered_at TIMESTAMPTZ,
  last_error_code TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox(status, next_attempt_at, outbox_id);
CREATE UNIQUE INDEX IF NOT EXISTS outbox_match_cursor_idx ON outbox(aggregate_id, (payload->>'cursor_user_id')) WHERE event_type='match_run';
CREATE UNIQUE INDEX IF NOT EXISTS outbox_notification_run_idx ON outbox(aggregate_id) WHERE event_type='notification_preview';

CREATE TABLE IF NOT EXISTS notification_event (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  product_id TEXT NOT NULL REFERENCES product(product_id),
  channel TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('preview','test','live')),
  status TEXT NOT NULL CHECK (status IN ('previewed','queued','sent','failed','suppressed')),
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at TIMESTAMPTZ,
  provider_message_id TEXT,
  error_code TEXT
);

CREATE TABLE IF NOT EXISTS audit_event (
  audit_id BIGSERIAL PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Capture mode at import; existing runs are deliberately left as previews.
ALTER TABLE run ADD COLUMN notification_mode TEXT NOT NULL DEFAULT 'preview'
  CHECK (notification_mode IN ('preview','test'));

CREATE TABLE notification_delivery (
  delivery_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('preview','test')),
  recipient TEXT,
  sender TEXT,
  status TEXT NOT NULL CHECK (status IN ('preview','ready','claimed','sending','sent','deferred','uncertain','failed')),
  claim_token UUID,
  lease_until TIMESTAMPTZ,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  send_started_at TIMESTAMPTZ,
  provider_message_id TEXT,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at TIMESTAMPTZ,
  UNIQUE (run_id,user_id),
  FOREIGN KEY (run_id,user_id) REFERENCES batch_user(run_id,user_id) ON DELETE CASCADE
);
CREATE INDEX notification_ready_idx ON notification_delivery(status,next_attempt_at,created_at);
CREATE TABLE notification_send_gate (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  next_send_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO notification_send_gate (singleton) VALUES (TRUE);

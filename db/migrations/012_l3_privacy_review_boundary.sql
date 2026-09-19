-- 012_l3_privacy_review_boundary.sql
-- Enforce one trusted Windmill approval per request key and evidence candidate.

create unique index if not exists uq_l3_privacy_review_idempotency
  on human_annotation ((value->>'idempotency_key'))
  where annotation_type='l3_privacy_review'
    and value->>'reviewer_identity_source'='windmill_end_user_email_allowlist_v1';

create unique index if not exists uq_l3_privacy_review_candidate
  on human_annotation (
    video_id,
    (value->>'version'),
    (value->>'evidence_fingerprint')
  )
  where annotation_type='l3_privacy_review'
    and value->>'reviewer_identity_source'='windmill_end_user_email_allowlist_v1';

-- 006_comment_text_rules.sql
-- Privacy-safe deterministic text-distribution and template signals.
-- Columns remain nullable so historical v1.0 snapshots do not acquire false zeroes.

alter table video_comment_feature_snapshot
  add column if not exists eligible_text_count integer
    check (eligible_text_count >= 0),
  add column if not exists normalized_unique_text_count integer
    check (normalized_unique_text_count >= 0),
  add column if not exists duplicate_text_count integer
    check (duplicate_text_count >= 0),
  add column if not exists duplicate_group_count integer
    check (duplicate_group_count >= 0),
  add column if not exists max_duplicate_group_size integer
    check (max_duplicate_group_size >= 0),
  add column if not exists url_text_count integer
    check (url_text_count >= 0),
  add column if not exists mention_text_count integer
    check (mention_text_count >= 0),
  add column if not exists emoji_only_text_count integer
    check (emoji_only_text_count >= 0),
  add column if not exists repeated_char_text_count integer
    check (repeated_char_text_count >= 0),
  add column if not exists short_text_count integer
    check (short_text_count >= 0),
  add column if not exists template_like_text_count integer
    check (template_like_text_count >= 0),
  add column if not exists char_bigram_count integer
    check (char_bigram_count >= 0),
  add column if not exists unique_char_bigram_count integer
    check (unique_char_bigram_count >= 0),
  add column if not exists top_char_bigram_count integer
    check (top_char_bigram_count >= 0),
  add column if not exists top_char_bigram_share numeric
    check (
      top_char_bigram_share is null
      or top_char_bigram_share between 0 and 1
    );

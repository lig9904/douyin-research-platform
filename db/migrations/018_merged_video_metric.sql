-- One display record per video; raw snapshots remain immutable.
-- Source priority is a presentation policy, not a claim that other zeros are invalid.
create or replace view merged_video_metric as
with candidates as (
  select m.video_id, m.id, m.captured_at, f.field, f.value,
    case when m.source_endpoint='douyin.billboard.low_fan' then 'billboard'
      when m.source_endpoint in ('douyin.app.multi_video_v2','douyin.app.multi_video',
        'douyin.app.one_video','douyin.app.video_statistics','douyin.app.multi_video_statistics') then 'detail'
      else 'other' end as source_kind,
    case when f.field in ('play_count','author_follower_count')
      and m.source_endpoint='douyin.billboard.low_fan' then 0 else 1 end as source_priority
  from metric_snapshot m
  cross join lateral (values
    ('play_count',m.play_count), ('like_count',m.like_count),
    ('comment_count',m.comment_count), ('share_count',m.share_count),
    ('collect_count',m.collect_count), ('author_follower_count',m.author_follower_count)
  ) f(field,value)
  where f.value is not null
), ranked as (
  select *, row_number() over (
    partition by video_id,field order by source_priority,captured_at desc,id desc
  ) as position from candidates
), selected as (
  select * from ranked where position=1
)
select video_id,
  max(value) filter(where field='play_count') as play_count,
  max(value) filter(where field='like_count') as like_count,
  max(value) filter(where field='comment_count') as comment_count,
  max(value) filter(where field='share_count') as share_count,
  max(value) filter(where field='collect_count') as collect_count,
  max(value) filter(where field='author_follower_count') as author_follower_count,
  max(captured_at) as captured_at,
  min(captured_at) as oldest_field_captured_at,
  'merged'::text as metric_source_kind,
  jsonb_object_agg(field,jsonb_build_object(
    'source_kind',source_kind,'captured_at',captured_at
  )) as metric_provenance
from selected group by video_id;

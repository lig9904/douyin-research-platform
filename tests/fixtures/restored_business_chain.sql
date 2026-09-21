-- Synthetic, database-only CI fixture. Never load into a deployed research DB.
-- No media object, provider request, or real human approval is created.
do $$
declare
  v uuid := gen_random_uuid();
  m uuid := gen_random_uuid();
  ac uuid := gen_random_uuid();
  lc uuid := gen_random_uuid();
  ak text := 'restore-ci-asr-' || v;
  lk text := 'restore-ci-l3-' || v;
  audio_hash text := repeat('a',64);
  review_hash text := repeat('b',64);
  input_hash text := repeat('c',64);
begin
  insert into source_video(id,platform,platform_video_id) values(v,'douyin','restore-ci-'||v);
  insert into media_asset(id,video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,content_type)
  values(m,v,'audio','synthetic-ci','synthetic-ci','sha256/aa/'||audio_hash,audio_hash,1,'audio/wav');
  insert into asr_media_review(asset_id,review_version,asset_fingerprint,delivery_origin,reviewed_by,identity_source)
  values(m,'synthetic-ci',review_hash,'https://invalid.example','synthetic-ci','windmill_end_user_email_allowlist_v1');
  insert into research_task_cost(id,task_key,task_type,task_version,video_id,status,input_fingerprint,cost_currency,cost_basis)
  values(ac,ak,'asr_transcription','synthetic-ci',v,'completed',audio_hash,'CNY','unknown'),
        (lc,lk,'l3_structured_research','synthetic-ci',v,'completed',input_hash,'CNY','unknown');
  insert into transcript(video_id,asr_provider,text_content,source_fingerprint,task_cost_id)
  values(v,'synthetic-ci','Synthetic CI transcript',audio_hash,ac);
  insert into asr_execution_job(id,task_key,video_id,provider,model_id,model_revision,engine_version,source_fingerprint,media_ref_fingerprint,status,cost_currency,budget_date,budget_key,task_cost_id)
  values(gen_random_uuid(),ak,v,'synthetic-ci','synthetic-ci','v1','v1',audio_hash,review_hash,'completed','CNY',current_date,'synthetic-ci',ac);
  insert into analysis_run(video_id,analysis_type,analysis_level,status,input_fingerprint,task_cost_id)
  values(v,'l3_structured_research','L3','completed',input_hash,lc);
  insert into l3_execution_job(id,task_key,video_id,provider,model_id,model_revision,prompt_version,schema_version,input_fingerprint,status,cost_currency,budget_date,budget_key,task_cost_id)
  values(gen_random_uuid(),lk,v,'synthetic-ci','synthetic-ci','v1','v1','v1',input_hash,'completed','CNY',current_date,'synthetic-ci',lc);
end $$;

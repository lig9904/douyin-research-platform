-- Optional comparison and counterexample videos for one project-owned decision.
-- References are frozen at creation, while current inclusion/availability is
-- reported separately so later withdrawal does not erase historical evidence.
create table if not exists project_decision_card_evidence_ref (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  position smallint not null check (position between 1 and 12),
  video_id uuid not null references source_video(id) on delete restrict,
  role text not null check (role in ('comparable', 'counterexample')),
  reason text not null check (char_length(reason) between 1 and 500 and reason = btrim(reason)),
  video_title_at_binding text,
  account_name_at_binding text,
  created_at timestamptz not null default now(),
  unique (decision_card_id, video_id),
  unique (decision_card_id, position),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict
);
create index if not exists idx_project_decision_card_evidence_ref_project_card
  on project_decision_card_evidence_ref(project_id, decision_card_id, position);

create or replace function enforce_project_decision_card_evidence_ref()
returns trigger language plpgsql as $$
declare
  primary_video uuid;
  card_created_at timestamptz;
  card_status text;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_evidence_ref is append-only';
  end if;
  -- Serializes concurrent additions to one card, including the 12-ref ceiling.
  select card.source_video_id, card.created_at, card.status
    into primary_video, card_created_at, card_status
  from project_decision_card card
  where card.id=new.decision_card_id and card.project_id=new.project_id
  for update;
  if primary_video is null then
    raise exception 'project_decision_card_evidence_ref requires a local decision card';
  end if;
  if card_created_at is distinct from transaction_timestamp()
     or card_status not in ('active', 'observing', 'excluded') then
    raise exception 'project_decision_card_evidence_ref must be bound when the card is created';
  end if;
  if new.video_id=primary_video then
    raise exception 'project_decision_card_evidence_ref cannot repeat primary video';
  end if;
  if (select count(*) from project_decision_card_evidence_ref ref
      where ref.decision_card_id=new.decision_card_id) >= 12 then
    raise exception 'project_decision_card_evidence_ref exceeds 12 videos';
  end if;
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    join source_video video_row on video_row.id=inclusion_row.video_id
    where inclusion_row.project_id=new.project_id
      and inclusion_row.video_id=new.video_id
      and inclusion_row.status='accepted'
      and video_row.availability_status='available'
  ) then
    raise exception 'project_decision_card_evidence_ref requires locally accepted available video';
  end if;
  select video_row.title, account_row.nickname
    into new.video_title_at_binding, new.account_name_at_binding
  from source_video video_row
  left join source_account account_row on account_row.id=video_row.account_id
  where video_row.id=new.video_id;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_evidence_ref on project_decision_card_evidence_ref;
create trigger trg_project_decision_card_evidence_ref
before insert or update or delete on project_decision_card_evidence_ref
for each row execute function enforce_project_decision_card_evidence_ref();

comment on table project_decision_card_evidence_ref is
  'Immutable project-local comparison or counterexample public video bound to a decision card; later withdrawal remains visible.';

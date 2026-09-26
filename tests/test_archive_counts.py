import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('counts', Path(__file__).resolve().parents[1] / 'scripts/test-server-archive-counts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_research_restore_inventory_includes_brief_control_plane():
    assert module.TARGETS['research'] == ('source_video', 'collection', 'collection_item')
    assert module.TARGETS['research_brief'] == ('research_brief', 'research_brief_run')
    assert module.TARGETS['project'] == (
        'research_organization', 'research_project', 'research_project_member',
        'research_subject', 'project_account_relation', 'account_group',
        'account_group_member', 'account_identity_link', 'account_authorization',
        'project_video_inclusion', 'project_video_share_grant', 'project_access_event',
    )
    assert module.TARGETS['project_024'] == module.TARGETS['project'][:10]
    assert module.TARGETS['subject_relevance_027'] == (
        'research_subject_term', 'project_video_subject_relevance',
        'project_video_subject_relevance_audit',
    )
    assert module.TARGETS['decision_loop_028'] == (
        'project_decision_card', 'project_decision_card_event',
        'project_publication_record', 'project_publication_metric_observation',
    )
    assert module.TARGETS['subject_score_029'] == ('project_video_subject_score',)


def test_copy_rows_not_field_values_or_escaped_newlines():
    lines = 'COPY public.source_video (id, title) FROM stdin;\n1\ttext\\nnext\n2\t\\N\n\\.\nCOPY public.collection (id) FROM stdin;\n\\.\n'
    assert module.counts(lines.splitlines(True), ('source_video','collection')) == '2|0'


def test_unselected_quoted_table_data_cannot_spoof_target_header():
    lines = 'COPY custom."OddName" (value) FROM stdin;\nCOPY public.source_video (id) FROM stdin;\n\\.\nCOPY public.source_video (id) FROM stdin;\n1\n\\.\n'
    assert module.counts(lines.splitlines(True), ('source_video',)) == '1'


def test_project_archive_inventory_counts_each_scoped_table_without_returning_rows():
    lines = ''.join(
        f'COPY public.{table} (id) FROM stdin;\n{value}\\.\n'
        for table, value in (
            ('research_organization', 'organization-a\n'),
            ('research_project', 'project-a\n'),
            ('research_project_member', 'member-a\nmember-b\n'),
            ('research_subject', ''),
            ('project_account_relation', ''),
            ('account_group', ''),
            ('account_group_member', ''),
            ('account_identity_link', ''),
            ('account_authorization', 'authorization-a\n'),
            ('project_video_inclusion', 'video-a\n'),
            ('project_video_share_grant', 'grant-a\n'),
            ('project_access_event', 'event-a\nevent-b\n'),
        )
    )
    assert module.counts(lines.splitlines(True), module.TARGETS['project']) == '1|1|2|0|0|0|0|0|1|1|1|2'
    assert module.counts(lines.splitlines(True), module.TARGETS['project_024']) == '1|1|2|0|0|0|0|0|1|1'


def test_subject_relevance_archive_inventory_counts_current_and_historical_rows():
    lines = ''.join(
        f'COPY public.{table} (id) FROM stdin;\n{rows}\\.\n'
        for table, rows in (
            ('research_subject_term', 'term-a\nterm-b\n'),
            ('project_video_subject_relevance', 'current-a\n'),
            ('project_video_subject_relevance_audit', 'audit-a\naudit-b\naudit-c\n'),
        )
    )
    assert module.counts(lines.splitlines(True), module.TARGETS['subject_relevance_027']) == '2|1|3'


def test_decision_loop_archive_inventory_preserves_revisions_and_reviews():
    lines = ''.join(
        f'COPY public.{table} (id) FROM stdin;\n{rows}\\.\n'
        for table, rows in (
            ('project_decision_card', 'card-a\n'),
            ('project_decision_card_event', 'create\nreview\n'),
            ('project_publication_record', 'publication-a\n'),
            ('project_publication_metric_observation', 'version-1\nversion-2\n'),
        )
    )
    assert module.counts(lines.splitlines(True), module.TARGETS['decision_loop_028']) == '1|2|1|2'


def test_subject_score_archive_inventory_preserves_run_scoped_rows():
    lines = (
        'COPY public.project_video_subject_score (id) FROM stdin;\n'
        'score-a\nscore-b\n\\.\n'
    )
    assert module.counts(lines.splitlines(True), module.TARGETS['subject_score_029']) == '2'


@pytest.mark.parametrize('value', ['', 'COPY public.source_video (id) FROM stdin;\n1\n',
    'COPY public.source_video (id) FROM stdin;\n\\.\nCOPY public.source_video (id) FROM stdin;\n\\.\n'])
def test_missing_truncated_duplicate_fail_closed(value):
    with pytest.raises(ValueError):
        module.counts(value.splitlines(True), ('source_video',))

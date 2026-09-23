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
        'project_video_inclusion',
    )


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
        )
    )
    assert module.counts(lines.splitlines(True), module.TARGETS['project']) == '1|1|2|0|0|0|0|0|1|1'


@pytest.mark.parametrize('value', ['', 'COPY public.source_video (id) FROM stdin;\n1\n',
    'COPY public.source_video (id) FROM stdin;\n\\.\nCOPY public.source_video (id) FROM stdin;\n\\.\n'])
def test_missing_truncated_duplicate_fail_closed(value):
    with pytest.raises(ValueError):
        module.counts(value.splitlines(True), ('source_video',))

import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('counts', Path(__file__).resolve().parents[1] / 'scripts/test-server-archive-counts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_copy_rows_not_field_values_or_escaped_newlines():
    lines = 'COPY public.source_video (id, title) FROM stdin;\n1\ttext\\nnext\n2\t\\N\n\\.\nCOPY public.collection (id) FROM stdin;\n\\.\n'
    assert module.counts(lines.splitlines(True), ('source_video','collection')) == '2|0'


def test_unselected_quoted_table_data_cannot_spoof_target_header():
    lines = 'COPY custom."OddName" (value) FROM stdin;\nCOPY public.source_video (id) FROM stdin;\n\\.\nCOPY public.source_video (id) FROM stdin;\n1\n\\.\n'
    assert module.counts(lines.splitlines(True), ('source_video',)) == '1'


@pytest.mark.parametrize('value', ['', 'COPY public.source_video (id) FROM stdin;\n1\n',
    'COPY public.source_video (id) FROM stdin;\n\\.\nCOPY public.source_video (id) FROM stdin;\n\\.\n'])
def test_missing_truncated_duplicate_fail_closed(value):
    with pytest.raises(ValueError):
        module.counts(value.splitlines(True), ('source_video',))

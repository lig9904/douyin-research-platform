#!/usr/bin/env python3
"""Count selected COPY rows from verified pg_restore output, without echoing data."""
import re
import sys


TARGETS = {
    'research': ('source_video', 'collection', 'collection_item'),
    'research_brief': ('research_brief', 'research_brief_run'),
    'project': (
        'research_organization', 'research_project', 'research_project_member',
        'research_subject', 'project_account_relation', 'account_group',
        'account_group_member', 'account_identity_link', 'account_authorization',
        'project_video_inclusion', 'project_video_share_grant', 'project_access_event',
    ),
    'project_024': (
        'research_organization', 'research_project', 'research_project_member',
        'research_subject', 'project_account_relation', 'account_group',
        'account_group_member', 'account_identity_link', 'account_authorization',
        'project_video_inclusion',
    ),
    'subject_relevance_027': (
        'research_subject_term', 'project_video_subject_relevance',
        'project_video_subject_relevance_audit',
    ),
    'decision_loop_028': (
        'project_decision_card', 'project_decision_card_event',
        'project_publication_record', 'project_publication_metric_observation',
    ),
    'project_decision_evidence_035': ('project_decision_card_evidence_ref',),
    'project_case_review_037': ('project_video_case_review',),
    'project_asr_standing_038': ('project_asr_standing_grant',),
    'subject_score_029': ('project_video_subject_score',),
    'project_private_analysis_032': (
        'project_asr_media_review', 'project_research_task_cost',
        'project_asr_execution_job', 'project_transcript',
        'project_l3_privacy_review', 'project_l3_execution_job',
        'project_l3_analysis_result',
    ),
    'windmill': ('workspace', 'usr'),
}


def counts(lines, tables):
    found = {}
    current = None
    for line in lines:
        if current is not None:
            if line.rstrip('\r\n') == r'\.':
                current = None
            elif current in tables:
                found[current] += 1
            continue
        match = re.fullmatch(r'COPY public\.([a-z0-9_]+) \(.*\) FROM stdin;\r?\n?', line)
        if re.fullmatch(r'COPY .* FROM stdin;\r?\n?', line):
            # Skip every COPY body, including quoted names and other schemas.
            # Its data must never be reinterpreted as a selected table header.
            current = match[1] if match else '__unselected_copy__'
            if current in tables:
                if current in found:
                    raise ValueError('duplicate COPY section')
                found[current] = 0
    if current is not None or set(found) != set(tables):
        raise ValueError('incomplete COPY inventory')
    return '|'.join(str(found[table]) for table in tables)


if __name__ == '__main__':
    try:
        print(counts(sys.stdin, TARGETS[sys.argv[1]]))
    except Exception:
        raise SystemExit('ERROR: archive row inventory could not be verified') from None

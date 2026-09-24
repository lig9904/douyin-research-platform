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
    'subject_score_029': ('project_video_subject_score',),
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
        match = re.fullmatch(r'COPY public\.([a-z_]+) \(.*\) FROM stdin;\r?\n?', line)
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

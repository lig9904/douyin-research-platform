#!/usr/bin/env python3
"""Count selected COPY rows from verified pg_restore output, without echoing data."""
import re
import sys


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
    targets = {'research': ('source_video','collection','collection_item'), 'windmill': ('workspace','usr')}
    try:
        print(counts(sys.stdin, targets[sys.argv[1]]))
    except Exception:
        raise SystemExit('ERROR: archive row inventory could not be verified') from None

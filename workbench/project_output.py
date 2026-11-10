"""Decode native output into data; never render SQL*Plus-generated HTML."""
import csv
import io
import re


def csv_table(output):
    records = []
    source = output.splitlines(keepends=True)
    reader = csv.reader(io.StringIO(output))
    previous = 0
    for row in reader:
        raw = ''.join(source[previous:reader.line_num]).strip()
        previous = reader.line_num
        if not row and records and len(records[0]) == 1:
            records.append([''])
            continue
        if not row:
            continue
        if not records:
            if not raw.startswith('"'):
                continue
            records.append(row)
        elif len(row) == len(records[0]) and (raw.startswith(('"', ',')) or re.match(r'^[+\-\d.]', raw)):
            records.append(row)
    if not records:
        return None
    return {'columns': records[0], 'rows': records[1:501], 'rowCount': len(records) - 1,
            'truncated': len(records) > 501}


def describe_table(output):
    raw_lines = output.splitlines()
    if raw_lines and not raw_lines[0].startswith(' '):
        raw_lines[0] = ' ' + raw_lines[0]
    lines = [line.expandtabs(8) for line in raw_lines]
    for index, line in enumerate(lines):
        spans = list(re.finditer(r'-{2,}', line))
        if len(spans) < 2 or not index:
            continue
        starts = [span.start() for span in spans]
        def cells(text):
            return [text[start:starts[i+1] if i+1 < len(starts) else None].strip()
                    for i, start in enumerate(starts)]
        headers = cells(lines[index-1])
        rows = [cells(text) for text in lines[index+1:] if text.strip()]
        if rows and any('Name' in header for header in headers):
            return {'columns': headers, 'rows': rows[:500], 'rowCount': len(rows), 'truncated': len(rows) > 500}
    return None

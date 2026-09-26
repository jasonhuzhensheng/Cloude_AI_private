"""Persisted global timeline, compiled into local segment instructions."""
import math
from .video_limits import FPS, SEGMENT_FRAMES


def validate_timeline(rows, duration):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('Timeline must contain at most 100 actions.')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each timeline action needs start, end and action.')
        start, end, action = row.get('start'), row.get('end'), row.get('action')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration:
            raise ValueError('Timeline times must satisfy 0 ≤ start < end ≤ output duration.')
        if not isinstance(action, str) or not 1 <= len(action.strip()) <= 40000:
            raise ValueError('Each timeline action must contain 1–40,000 characters.')
        result.append(dict(start=start, end=end, action=action.strip()))
    result.sort(key=lambda row: row['start'])
    if any(a['end'] > b['start'] for a, b in zip(result, result[1:])):
        raise ValueError('Timeline actions cannot overlap.')
    if sum(len(row['action']) for row in result) > 40000:
        raise ValueError('Timeline actions must total at most 40,000 characters.')
    return result


def segment_prompt(job, index):
    rows = getattr(job, 'timeline', [])
    if not rows:
        return job.prompt
    start = index * SEGMENT_FRAMES / FPS
    end = min((index + 1) * SEGMENT_FRAMES / FPS, job.duration_seconds)
    lines = [job.prompt, '\nTimeline for this clip (times below are seconds from this clip start).',
             'Preserve the established subjects and scene. Continue from the input frame. '
             'During unlisted time, maintain the current state with natural idle motion; do not restart completed actions.']
    for row in rows:
        if row['start'] < end and row['end'] > start:
            a, b = max(row['start'], start)-start, min(row['end'], end)-start
            verb = 'Continue the action already underway' if row['start'] < start else 'Begin this action'
            lines.append(f"{a:.3f}–{b:.3f}s: {verb}: {row['action']}")
    return '\n'.join(lines)


def draft_timeline(text, duration):
    """Rule-based draft; never invokes disabled chat or consumes GPU time."""
    import re
    if type(duration) is not int or not 1 <= duration <= 1800:
        raise ValueError('Choose an output duration from 1 to 1,800 seconds.')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 40000:
        raise ValueError('Enter an action prompt of 1–40,000 characters.')
    # Explicit ranges, in seconds, may appear on separate lines or inline.
    pattern = r'(?<![\d.:])(\d+(?:\.\d+)?)\s*(?:秒|s(?:ec(?:onds?)?)?)?\s*[-–—~至到]\s*(\d+(?:\.\d+)?)\s*(?:秒|s(?:ec(?:onds?)?)?)?\s*[:：]?'
    matches = list(re.finditer(pattern, text, re.I))
    if matches:
        if text[:matches[0].start()].strip():
            raise ValueError('Put shared scene details in the main prompt; start timed actions with a time range.')
        rows = []
        for i, match in enumerate(matches):
            action = text[match.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip(' \n\r;；,，')
            rows.append(dict(start=float(match[1]), end=float(match[2]), action=action))
        return validate_timeline(rows, duration), 'Explicit time ranges preserved. Review the actions before generating.'
    # Keep simultaneous clauses together; only sequential connectors/sentences split.
    actions = [part.strip(' \t,，:：。.') for part in re.split(r'\n+|[;；。]+|(?<=[.!?])\s+|(?:然后|接着|随后|最后)|\b(?:then|next|finally)\b', text, flags=re.I)]
    actions = [re.sub(r'^(?:首先|先|first\b)\s*[,，:：]?\s*', '', a, flags=re.I) for a in actions if a]
    actions = [a for a in actions if a]
    if not actions:
        raise ValueError('Enter at least one action.')
    rows = [dict(start=round(i*duration/len(actions), 6), end=round((i+1)*duration/len(actions), 6), action=a) for i,a in enumerate(actions)]
    return validate_timeline(rows, duration), 'Actions split in order and given equal time. Review timing and scene details before generating.'

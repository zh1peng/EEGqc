def merged_intervals(intervals):
    """Return disjoint source-sample intervals; stops are exclusive."""
    result = []
    for start, stop in sorted((d.start, d.stop) for d in intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(stop, result[-1][1]))
        else:
            result.append((start, stop))
    return result


def retained_segments(sample_count, removed):
    result, cursor, output = [], 0, 0
    for start, stop in [*removed, (sample_count, sample_count)]:
        if start > cursor:
            result.append((cursor, start, output))
            output += start - cursor
        cursor = stop
    return result

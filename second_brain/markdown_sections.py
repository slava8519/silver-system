"""Keep fenced Markdown examples outside the sections managed by card editors."""

import re


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)")


def fenced_lines(lines):
    """Return the protected line indices and a safe position for appending new sections."""
    code = set()
    fence = ""
    opening = len(lines)
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence:
            code.add(index)
            if match and match[1].startswith(fence) and not match[2].strip():
                fence = ""
                opening = len(lines)
        elif match and (match[1][0] != "`" or "`" not in match[2]):
            fence = match[1]
            opening = index
            code.add(index)
    return code, opening

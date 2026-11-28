"""Keep fenced Markdown examples outside the sections managed by card editors."""

import re


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)")


def fenced_lines(lines, *, comments=False):
    """Return the protected line indices and a safe position for appending new sections."""
    code = set()
    fence = ""
    opening = len(lines)
    comment = False
    for index, line in enumerate(lines):
        if comments and not fence and (comment or _COMMENT.match(line)):
            if not comment:
                opening = index
            code.add(index)
            comment = "-->" not in line
            if not comment:
                opening = len(lines)
            continue
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


_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_NON_PARAGRAPH = re.compile(r"^(?: {4}| {0,3}(?:\t|>|[-+*]\s|\d+[.)]\s|<|\[[^\]]+\]:))")
_COMMENT = re.compile(r"^ {0,3}<!--")


def headings(lines, *, start=0):
    """Map an ATX/Setext heading's first line to (level, title, first body line).

    Coordinates are zero-based. Setext underlining can close a multi-line
    paragraph; its whole title belongs to the following section.
    """
    plain = [text.rstrip("\r\n") for text in lines]
    fenced, _ = fenced_lines(plain, comments=True)
    found = {}
    paragraph = None
    for i in range(start, len(plain)):
        line = plain[i]
        if i in fenced:
            paragraph = None
            continue
        atx = _ATX.match(line)
        if atx:
            title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", atx[2] or "").strip()
            found[i] = (len(atx[1]), title, i+1)
            paragraph = None
            continue
        underline = _SETEXT.match(line)
        if underline:
            if paragraph is not None:
                title = " ".join(part.strip() for part in plain[paragraph:i])
                found[paragraph] = (1 if underline[1].startswith("=") else 2, title, i+1)
            paragraph = None
        elif not line.strip() or _NON_PARAGRAPH.match(line):
            paragraph = None
        elif paragraph is None:
            paragraph = i
    return found

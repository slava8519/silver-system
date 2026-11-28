"""Select source lines by subject, record names and explicitly recorded decisions."""

import re
from datetime import date
from difflib import SequenceMatcher
from typing import NamedTuple

from .search import visible_body_lines, word_stems


# These words express the request rather than the subject being retrieved.
_FILLER = set(word_stems(
    "мы нам нас меня мне мой наши что-нибудь там это про об этом какие каких "
    "напомни пожалуйста расскажи покажи найди узнать знаем известно есть записано именно "
    "заметка запись проект проекты проектов направлении проектах проектам поводу вместе оба обоих "
    "какова каков каковы каким команда команды будем будет было спустя "
    "кто кого кому кем когда куда откуда почему "
    "we did do does have has our the and are was were who when why how projects project please tell me about both"
))
_LOCATION_QUESTION = re.compile(r"\b(?:где|where)\b", re.I)
_LOCATION_VERBS = set(word_stems("лежит лежат находится находятся хранится хранятся расположен расположена "
                                 "расположено расположены найти stored located kept find"))
_LOCATION_REQUEST = re.compile(r"\b(где|where)\s+(?:я могу|можно|мне|can I)\s+(?:найти|find)\s+"
                               r"(?:(?:сво[йюёеи]|свои|my)\s+)?", re.I)
_REQUEST_INTRO = re.compile(
    r"^(?:(?:(?:дай|ответь|покажи|расскажи|изложи)\s+(?:кратко|коротко|вкратце)"
    r"(?!\s+(?:о|об|по|про)\b)|(?:кратко|коротко|вкратце)(?=\s*(?:[:,]|"
    r"(?:расскажи|ответь|покажи|скажи|сколько|как\w*|кто|что|где|когда)\b))|расскажи(?:те)?|пожалуйста|ответь(?:те)?|(?:не )?подскаж(?:и(?:те)?|ешь|ете)|скажи(?:те)?|"
    r"посмотри(?:те)?|(?:можешь|можете|можно)\s+(?:подсказать|сказать|узнать)|"
    r"кто-нибудь знает|(?:can|could) you (?:please )?tell me)[\s,:]+)+", re.I)
_DECISION = re.compile(r"^(?:реш(?:ил|ен)|договор|согласов|утвер|выбра|приня|decid|decision|agre|approv|chosen)")
_GOAL = re.compile(r"^(?:цел(?:ь|и|я)?$|задач|goals?$|objectives?$)")
_GOAL_LINE = re.compile(r"^\s*(?:[-*#]\s*)*(?:цел[ьи]|цель|goals?|objectives?)\b", re.I)
# A recorded operating rule may use an affirmative action with a condition
# instead of the word "decision": e.g. a required check before a handoff.
_RULE = re.compile(r"^\s*(?:[-*]\s*)?(?:(?:перед|при|после|до|если|когда)\s+.+?\b"
                   r"(?:проверя|проверить|выполня|переда[её]|сохраня|отправля|подтвержда|сверя|обяз)|"
                   r"(?:before|after|when|if)\s+.+?\b(?:must|shall|always)\b)", re.I)
_PENDING = re.compile(r"\b(?:предстоит|предложено|предлагается|надо решить|ещ[её] не|пока не|"
                      r"не (?:принят|согласован|утвержд)|to be decided|undecided|proposed)\b", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+)")
_NEW_QUESTION = re.compile(r"\s+(?:и|а|and|but)\s+(?=(?:что|как\w*|где|куда|когда|чем|сколько|кто|what|where|when|how|who)\b)|[?;]+", re.I)
_QUOTED_NAME = re.compile(r'«[^»]+»|"[^"]+"|„[^“]+“|“[^”]+”')
_SUBJECT_BREAK = re.compile(_QUOTED_NAME.pattern + r"|(?P<join>\s+(?:и|and)\s+)", re.I)
_CITATION_REQUEST = re.compile(
    r"(?:(?:дай|укажи|покажи|приведи|добавь)\s+(?:ссылк[уи]|источник[и]?)|со\s+ссылк(?:ой|ами)|"
    r"(?:cite|link to|give|include)\s+(?:the\s+)?sources?)\b", re.I)
_CITATION_WORDS = set(word_stems(
    "запись записи каждую каждого каждой решения факта ответа файл строку отдельно "
    "на для по к each every decision fact answer record file line for"))
_CURRENT = set(word_stems("текущий сейчас сегодня актуальный current now currently today"))
_HISTORICAL_REQUEST_WORDS = set(word_stems("раньше ранее прежде прежний предыдущий история истории архив архивный "
                                            "earlier former previous previously history archived"))
_EXPLICIT_BRIEF = re.compile(r"\b(?:коротко|кратко|вкратце|сводк\w*|резюме|summary)\b", re.I)
_BRIEF_REQUEST = re.compile(
    r"\b(?:коротко|кратко|вкратце|сводк\w*|резюме|где (?:мы |проект )?(?:сейчас|находимся)|"
    r"где сейчас|как (?:сейчас |обстоят |идут )*дела|что (?:сейчас )?происходит|"
    r"текущ(?:ая|ую) (?:картин\w*|ситуаци\w*)|в каком состоянии|что с проектом|"
    r"как (?:продвигается|идёт|идет) проект|"
    r"на каком (?:мы )?этапе|"
    r"where (?:are we|do we stand)|project summary|current situation)\b", re.I)
_EVENT_REQUEST = re.compile(
    r"\b(?:что (?:там |недавно |последним )?(?:изменил\w*|поменял\w*|нового|произошло|случилось)|"
    r"(?:последн\w*|недавн\w*|свеж\w*) (?:изменени\w*|событи\w*|новост\w*)|"
    r"истори(?:я|ю|и)\s+(?:по|проекта|событий|изменений)|хроник\w*|событи[яй]|"
    r"какие (?:были )?изменения|последнее по|"
    r"what(?: has|'s)? changed|what.s new|recent (?:events|changes))\b", re.I)
_ISO_DAY = re.compile(r"(?<!\w)\d{4}-\d{2}-\d{2}(?!\w)")
_EVENT_INTERVAL = re.compile(r"\s*(?:по|до|to|through|and|[–—-]|\.\.)\s*", re.I)
_TIME_HINT = re.compile(
    r"\b(?:сегодня|вчера|позавчера|завтра|назад|прошл\w*|минувш\w*|недел\w*|месяц\w*|"
    r"квартал\w*|полугод\w*|год[ауы]?|лет|дн(?:я|ей|и)?|сут(?:ки|ок)|час(?:а|ов|ы)?|"
    r"весной|летом|осенью|зимой|выходн\w*|"
    r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяею]|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|понедельник\w*|вторник\w*|сред[уы]|"
    r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|за|между|после|перед|раньше|позже|"
    r"с (?:начала|момента)|к моменту|"
    r"today|yesterday|last|week\w*|month\w*|year\w*|before|after|during|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"\d{4}|\d{1,2}[./-]\d{1,2})\b", re.I)
_PERIOD_HELP = ("Уточните период событий датой ГГГГ-ММ-ДД или диапазоном, например "
                "«с 2026-11-01 по 2026-11-25». Обе границы входят в период.")
_STATE_REQUEST = {frozenset(word_stems(phrase)): field for field, phrases in (
    ("status", ("статус", "состояние", "status", "state")),
    ("due", ("срок", "дедлайн", "deadline", "due", "due date")),
    ("next_step", ("следующий шаг", "ближайший шаг", "что делать дальше", "что дальше", "next step", "what comes next")),
    ("owner", ("владелец", "кто ведёт", "кто отвечает", "owner", "who owns")),
) for phrase in phrases}
_PROJECT_COUNTS = set(word_stems("двух двоих трёх четырёх всех two three four all"))
_NUMBER = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4}
_INTERVAL = re.compile(
    r"\b(?:(\d+|один|одну|одна|два|две|три|четыре)\s+)?"
    r"(сутки|суток|час(?:а|ов)?|минут(?:у|ы)?|день|дня|дней)\b", re.I
)


def _intervals(text):
    values = set()
    for number, unit in _INTERVAL.findall(text.casefold()):
        count = int(number) if number.isdigit() else _NUMBER.get(number, 1)
        minutes = 60 if unit.startswith("час") else 1 if unit.startswith("минут") else 1440
        values.add(count * minutes)
    return values


class _Mention(NamedTuple):
    start: int
    end: int
    name: str
    complete: bool


def _mentions(words, record, *, state_query=False):
    # A neuter Russian project name such as «Озеро» keeps the final о in
    # the search tokenizer while its oblique forms («Озера») lose the ending.
    def comparable(tokens):
        if state_query and record["type"] in ("project", "project_history"):
            return [t[:-1] if len(t) > 3 and t.endswith("о") else t for t in tokens]
        return tokens

    matches = []
    for name in [*record["aliases"], record["title"]]:
        phrase = list(word_stems(name))
        if phrase:
            match = SequenceMatcher(None, comparable(words), comparable(phrase), autojunk=False).find_longest_match(
                0, len(words), 0, len(phrase))
            if match.size == len(phrase) or (name == record["title"] and match.size >= 2):
                matches.append(_Mention(match.a, match.a + match.size, name, match.size == len(phrase)))
    return max(matches, key=lambda m: (m.complete, m.end - m.start), default=None)


def _named_records(tokens, records, state_query):
    matches = {r["id"]: m for r in records if (m := _mentions(tokens, r, state_query=state_query))}
    complete = {(m.start, m.end) for m in matches.values() if m.complete}
    return {key: m for key, m in matches.items() if not any(
        start <= m.start and m.end <= end and (not m.complete or (start, end) != (m.start, m.end))
        for start, end in complete)}


def _subjects(text):
    """Split conjunctions outside quoted names."""
    start = 0
    parts = []
    for match in _SUBJECT_BREAK.finditer(text):
        if match.lastgroup == "join":
            parts.append(text[start:match.start()])
            start = match.end()
    return [*parts, text[start:]]


def _quoted_projects(quoted, projects):
    name = list(word_stems(quoted[0][1:-1]))
    return {key: m for key, m in _named_records(name, projects, True).items()
            if m.complete and m.start == 0 and m.end == len(name)}


def _clauses(query, records):
    """Bind standalone state fields to projects named in the same question."""
    for sentence in re.split(r"[?;]+", query):
        sentence = _REQUEST_INTRO.sub("", sentence.strip())
        quoted_names = list(_QUOTED_NAME.finditer(sentence))
        if len(quoted_names) > 1:
            request = re.sub(r"\b(?:и|and)\b", " ", _QUOTED_NAME.sub(" ", sentence), flags=re.I).strip()
            if (frozenset(word_stems(request)) - _FILLER - _CURRENT) in _STATE_REQUEST:
                projects = [r for r in records if r["type"] in ("project", "project_history")]
                for quoted in quoted_names:
                    matches = _quoted_projects(quoted, projects)
                    scope = {key: _Mention(0, 0, m.name, True) for key, m in matches.items()}
                    yield request if matches else quoted[0], scope
                continue
        parts = _subjects(sentence)
        bare = {part for part in parts if (frozenset(word_stems(part)) - _FILLER - _CURRENT) in _STATE_REQUEST}
        anchors = [part for part in parts if part not in bare]
        if bare and len(anchors) == 1:
            anchor = anchors[0]
            projects = [r for r in records if r["type"] in ("project", "project_history")]
            mentions = _named_records(list(word_stems(anchor)), projects, True)
            explicit = re.search(r"\b(?:проект\w*|projects?)\b", anchor, re.I)
            if explicit:
                quoted = _QUOTED_NAME.match(anchor[explicit.end():].lstrip())
                if quoted:
                    matches = _quoted_projects(quoted, projects)
                    mentions = {key: m for key, m in mentions.items() if key in matches}
            if mentions or explicit:
                # The actual name occurs in the anchor. On the other parts it
                # contributes no word positions and only restricts the subject.
                scope = {key: _Mention(0, 0, m.name, m.complete) for key, m in mentions.items()}
                for part in parts:
                    yield part, scope if part in bare else mentions
                continue
        for clause in _NEW_QUESTION.split(sentence):
            if clause.strip():
                yield clause, None


def _event_period(query):
    dates = list(_ISO_DAY.finditer(query))
    if not dates:
        if _TIME_HINT.search(query):
            raise ValueError(_PERIOD_HELP)
        return (None, None)
    try:
        days = [date.fromisoformat(match[0]).isoformat() for match in dates]
    except ValueError:
        raise ValueError(_PERIOD_HELP) from None
    prefix, suffix = query[:dates[0].start()], query[dates[-1].end():]
    between = bool(re.search(r"\bbetween\s*$", prefix, re.I))
    prefix = re.sub(r"\b(?:за|на|between)\s*$", "", prefix, flags=re.I)
    if _TIME_HINT.search(prefix + " " + suffix):
        raise ValueError(_PERIOD_HELP)
    if len(days) == 1:
        if re.search(r"\b(?:с|от|from|since)\s*$", prefix, re.I):
            return days[0], None
        if re.search(r"\b(?:по|до|until|through)\s*$", prefix, re.I):
            return None, days[0]
        return days[0], days[0]
    if len(days) == 2 and days[0] <= days[1]:
        separator = query[dates[0].end():dates[1].start()]
        if _EVENT_INTERVAL.fullmatch(separator) and (separator.strip().lower() != "and" or between):
            return days[0], days[1]
    raise ValueError(_PERIOD_HELP)


def _project_spans(query, records):
    """Map whole project names to character spans; a quoted name is indivisible."""
    def tokens(value):
        return [word[:-1] if len(word) > 3 and word.endswith("о") else word for word in word_stems(value)]

    names = {}
    for record in records:
        if record["type"] == "project":
            for name in [record["title"], *record["aliases"]]:
                phrase = tuple(tokens(name))
                if phrase:
                    names.setdefault(phrase, {})[record["id"]] = name
    quoted = [(m.start(), m.end(), names.get(tuple(tokens(m[0][1:-1])), {}))
              for m in _QUOTED_NAME.finditer(query)]
    words, positions = [], []
    for term in re.finditer(r"[^\W_]+", query):
        for word in tokens(term[0]):
            words.append(word)
            positions.append(term.span())
    result = list(quoted)
    for phrase, projects in names.items():
        for start in range(len(words) - len(phrase) + 1):
            end = start + len(phrase)
            if tuple(words[start:end]) == phrase:
                a, b = positions[start][0], positions[end - 1][1]
                if not any(left < b and a < right for left, right, _ in quoted):
                    result.append((a, b, projects))
    return sorted((a, b, group) for a, b, group in result if not any(
        left <= a and b <= right and (a, b) != (left, right) for left, right, _ in result))


def _without_names(text, spans):
    for a, b, _ in reversed(spans):
        text = text[:a] + "\ufffc" * (b - a) + text[b:]
    return text


def project_field_request(query, projects):
    """Resolve an explicit field and project before considering matching prose."""
    query = _REQUEST_INTRO.sub("", query.strip())
    words = set(word_stems(query))
    if not any(phrase <= words for phrase in _STATE_REQUEST):
        return None
    spans = _project_spans(query, projects)
    if not spans:
        return None
    # A project may itself be called «Статус». A quoted reference takes precedence
    # over an unquoted copy of its name used as the predicate of the question.
    quoted = [s for s in spans if _QUOTED_NAME.fullmatch(query[s[0]:s[1]])]
    if quoted:
        quoted_ids = {ident for _, _, names in quoted for ident in names}
        spans = [s for s in spans if s in quoted or not (quoted_ids & s[2].keys())
                 or frozenset(word_stems(query[s[0]:s[1]])) not in _STATE_REQUEST]
    predicate = _without_names(query, spans)
    # Preserve which field belongs to which project in compound requests.
    boundaries = [m.span() for m in re.finditer(r"\b(?:и|and)\b", predicate, re.I)]
    groups = []
    for start, end in zip([0, *(b for _, b in boundaries)], [*(a for a, _ in boundaries), len(query)]):
        terms = frozenset(word_stems(predicate[start:end])) - _FILLER - _CURRENT
        if len(spans) > 1:
            terms -= _PROJECT_COUNTS
        field = _STATE_REQUEST.get(terms)
        if terms and field is None:
            return None
        groups.append((field, [s for s in spans if start <= s[0] and s[1] <= end]))
    fields = [field for field, _ in groups if field]
    if not fields:
        return None
    # A single field can be shared by several names and vice versa. Independent
    # field/name pairs keep their own bindings.
    bindings = [i for i, (field, named) in enumerate(groups) if field and named]
    targets, gaps = [], []
    for position, (field, named) in enumerate(groups):
        if not field:
            continue
        if len(bindings) <= 1:
            named = spans
        elif not named:
            nearest = min(bindings, key=lambda i: abs(i - position))
            named = groups[nearest][1]
        for a, b, names in named:
            if len(names) == 1:
                ident, label = next(iter(names.items()))
                targets.append((ident, label, field))
            elif names:
                gaps.append("Название относится к нескольким проектам. Уточните направление или название проекта.")
            else:
                gaps.append(f"Проект {query[a:b]} не найден.")
    return list(dict.fromkeys(targets)), " ".join(dict.fromkeys(gaps))


def split_update_question(query, projects):
    """Keep both a fact and a journal request when they share a named project."""
    query = _REQUEST_INTRO.sub("", query.strip())
    if not (_BRIEF_REQUEST.search(query) or _EVENT_REQUEST.search(query)):
        return None
    spans = _project_spans(query, projects)
    if not spans:
        return None
    masked = _without_names(query, spans)
    for boundary in _NEW_QUESTION.finditer(masked):
        sides = [(0, boundary.start()), (boundary.end(), len(query))]
        update = [bool(_BRIEF_REQUEST.search(masked[a:b]) or _EVENT_REQUEST.search(masked[a:b])) for a, b in sides]
        named = [any(a <= x and y <= b for x, y, _ in spans) for a, b in sides]
        if not any(update) or (all(update) and not all(named)):
            continue
        parts = [query[a:b].strip(" ?!;,.") for a, b in sides]
        if not all(parts) or any(_CITATION_REQUEST.match(part) for part in parts):
            continue
        for i, part in enumerate(parts):
            # The journal needs a named project. An independently worded fact
            # question, e.g. «Где ключ?», keeps its original search scope.
            if not named[i] and (update[i] or re.search(r"\b(?:эт\w* проект\w*|его|у него)\b", part, re.I)):
                labels = [query[x:y].strip('«»"„“”') for x, y, _ in spans]
                parts[i] += " по проекту " + " и ".join(f"«{label}»" for label in labels)
        return tuple(parts)
    return None


def project_update_request(query, records):
    """Select explicitly named projects for questions about their brief or event log."""
    query = _REQUEST_INTRO.sub("", query.strip())
    patterns = (("brief", _BRIEF_REQUEST), ("event", _EVENT_REQUEST))
    if not any(pattern.search(query) for _, pattern in patterns):
        return None
    spans = _project_spans(query, records)
    predicate = _without_names(query, spans)
    fields = [kind for kind, pattern in patterns if pattern.search(predicate)]
    if not fields:
        return None
    matches, gaps = {}, []
    for a, b, names in spans:
        if len(names) > 1:
            gaps.append(f"Название {query[a:b]} относится к нескольким проектам. Уточните направление или название проекта.")
        elif not names:
            gaps.append(f"Проект {query[a:b]} не найден.")
        else:
            matches.update(names)
    if matches:
        period = (None, None)
        if "event" in fields:
            # A date can be part of a project title, e.g. «Форум 2026».
            for start, end, _ in reversed(spans):
                query = query[:start] + " [проект] " + query[end:]
            try:
                period = _event_period(query)
            except ValueError as error:
                return fields, {}, str(error), period
        return fields, matches, " ".join(dict.fromkeys(gaps)), period
    if gaps:
        return fields, {}, " ".join(dict.fromkeys(gaps)), (None, None)
    rest = _EVENT_REQUEST.sub("", _BRIEF_REQUEST.sub("", query))
    if re.search(r"\b(?:проект\w*|projects?)\b", query, re.I) or not (set(word_stems(rest)) - _FILLER):
        return fields, {}, "Уточните название или другое имя проекта и при необходимости его направление.", (None, None)
    return None


def asks_for_brief(query):
    return bool(_EXPLICIT_BRIEF.search(query))


def _related(term, words):
    if term in words:
        return True
    # A noun and a related verb can have different endings: «вход»/«входить».
    return any(min(len(term), len(w)) >= 4 and (w.startswith(term) or term.startswith(w))
               for w in words)


def _best_line(record, details, interval, decisions, goals, named):
    history = record.get("history_only", False)
    if named and record["type"] == "project" and not (history or interval or decisions or goals):
        field = _STATE_REQUEST.get(frozenset(details - _CURRENT))
        if field:
            # Cleared fields stay unknown even when the body retains a history.
            if not record.get(field):
                return None
            return (len(details), 0, 1), record["field_lines"][field]
    best = None
    sections = []
    status_request = history and named and _STATE_REQUEST.get(frozenset(details)) == "status"
    status_ranges = [range(span["archiveStart"], span["archiveStart"] + span["end"] - span["start"] + 1)
                     for span in record.get("archiveOrigin", {}).get("ranges", ())
                     if status_request and span["section"] == "История статусов"]
    for n, line in visible_body_lines(record, history=history):
        heading = _HEADING.match(line)
        if heading:
            level = len(heading[1])
            while sections and sections[-1][0] >= level:
                sections.pop()
            sections.append((level, heading[2]))
            continue
        if not line.strip() or (interval and not interval <= _intervals(line)):
            continue
        words = set(word_stems(line))
        if record.get("search_kind"):
            words.update(word_stems(record["search_kind"]))
        if status_request:
            words.update(word_stems(" ".join(title for _, title in sections)))
            if any(n in span for span in status_ranges):
                words.update(details)
        overlap = sum(_related(term, words) for term in details)
        goal_line = bool(_GOAL_LINE.search(line))
        if goals and not goal_line:
            continue
        if decisions and goal_line:
            continue
        # If the user supplied a subject beyond the record name (e.g. budget),
        # the line must contain evidence about that subject.
        if overlap < len(details):
            continue
        if not named and not overlap and not goals and not decisions and not interval:
            continue
        decision_rank = 0
        if decisions:
            context = " ".join(heading_text for _, heading_text in sections)
            if line.rstrip().endswith("?") or _PENDING.search(line) or _PENDING.search(context):
                continue
            if any(_DECISION.match(w) for w in words) or any(_DECISION.match(w) for w in word_stems(context)):
                decision_rank = 2
            elif _RULE.match(line):
                decision_rank = 1
            else:
                continue
        score = (overlap, decision_rank, int(not goal_line))
        if best is None or score > best[0]:
            best = (score, n)
    return best


def passages(query, records, *, candidates_for=None, history=False):
    """Return distinct source lines in question order and any unanswered parts."""
    picked = {}
    missing = []
    for clause, inherited_projects in _clauses(query, records):
        if inherited_projects == {}:
            missing.append(clause.strip())
            continue
        clause = _REQUEST_INTRO.sub("", clause.strip())
        clause = _LOCATION_REQUEST.sub(r"\1 ", clause)
        # Citations already accompany every answer. A trailing formatting request is
        # a separate search only if it also names a subject outside that request.
        citation = _CITATION_REQUEST.match(clause.strip())
        if (picked or missing) and citation and set(word_stems(clause.strip()[citation.end():])) <= _CITATION_WORDS:
            continue
        # Separate parallel subjects so an unknown/deleted alias cannot suppress
        # the answer for the remaining record. Bare names inherit the question.
        subjects = _subjects(clause.strip())
        words = set(word_stems(clause))
        state_query = any(phrase <= words for phrase in _STATE_REQUEST)
        if (len(subjects) > 1 and not all(any(field <= set(word_stems(part)) for field in _STATE_REQUEST)
                                             for part in subjects)
                and all(any(_mentions(list(word_stems(part)), r, state_query=state_query) for r in records)
                        for part in subjects)):
            # The predicate may follow the names: «У А и Б какой бюджет?».
            subjects = [clause]
        previous = (set(), set(), False, False)
        for subject in subjects:
            # The requested interval is verified separately. Its words must not
            # count as evidence for a different subject such as a price.
            interval = _intervals(subject)
            tokens = list(word_stems(_INTERVAL.sub(" ", subject)))
            if not tokens and not interval:
                continue
            mentions = inherited_projects or _named_records(tokens, records, state_query)
            names = {word for m in mentions.values() for word in tokens[m.start:m.end]}
            terms = set(tokens) - names - _FILLER
            if history:
                terms -= _HISTORICAL_REQUEST_WORDS
            decisions = any(_DECISION.match(t) for t in terms)
            goals = any(_GOAL.match(t) for t in terms)
            details = {t for t in terms if not _DECISION.match(t) and not _GOAL.match(t)}
            if _LOCATION_QUESTION.search(subject) and details - _LOCATION_VERBS:
                details -= _LOCATION_VERBS
            if mentions and not terms and not interval:
                details, interval, decisions, goals = previous
            previous = details, interval, decisions, goals
            candidates = []
            rows = candidates_for(mentions, details, decisions) if candidates_for is not None else records
            for record in rows:
                rid = record["id"]
                mention = mentions.get(rid)
                if mentions and not mention:
                    continue
                best = _best_line(record, details, interval, decisions, goals, bool(mention))
                if best:
                    label = mention.name if mention else next(iter(record["aliases"]), record["title"])
                    candidates.append((best[0], mention.start if mention else 0, rid, label, best[1]))
                elif mention:
                    missing.append(mention.name)
            if not candidates and not mentions:
                missing.append(subject.strip())
                prefix = re.split(r"\b(?:по|про|об?|у|с)\s+", subject, maxsplit=1, flags=re.I)[0]
                if prefix != subject:
                    # In «решили по X и Y» a missing X is not a condition on Y.
                    previous = details & set(word_stems(prefix)), interval, decisions, goals
            candidates.sort(key=lambda c: c[0], reverse=True)
            if mentions:
                candidates.sort(key=lambda c: c[1])
            for score, _, rid, label, n in candidates:
                if not mentions and score[0] < candidates[0][0][0]:
                    break
                picked.setdefault((rid, n), label)
    return picked, list(dict.fromkeys(missing))

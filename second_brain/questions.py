"""Select source lines by subject, record names and explicitly recorded decisions."""

import re
from datetime import date
from difflib import SequenceMatcher

from .search import visible_body_lines, word_stems


# These words express the request rather than the subject being retrieved.
_FILLER = set(word_stems(
    "мы нам нас меня мне мой наши что-нибудь там это про об этом какие каких "
    "напомни пожалуйста расскажи покажи найди узнать знаем известно есть записано "
    "заметка запись проект проекты направлении проектах проектам поводу вместе оба обоих "
    "какова каков каковы каким команда команды будем будет было спустя "
    "we did have has our the and are was were projects project please tell me about both"
))
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
_NEW_QUESTION = re.compile(r"\s+(?:и|and)\s+(?=(?:что|как\w*|где|куда|когда|чем|сколько|кто|what|where|when|how|who)\b)|[?;]+", re.I)
_CITATION_REQUEST = re.compile(
    r"(?:(?:дай|укажи|покажи|приведи|добавь)\s+(?:ссылк[уи]|источник[и]?)|со\s+ссылк(?:ой|ами)|"
    r"(?:cite|link to|give|include)\s+(?:the\s+)?sources?)\b", re.I)
_CITATION_WORDS = set(word_stems(
    "запись записи каждую каждого каждой решения факта ответа файл строку отдельно "
    "на для по к each every decision fact answer record file line for"))
_CURRENT = set(word_stems("текущий сейчас сегодня актуальный current now currently today"))
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
    ("next_step", ("следующий шаг", "ближайший шаг", "next step")),
) for phrase in phrases}
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


def _mentions(words, record, *, state_query=False):
    # A neuter Russian project name such as «Озеро» keeps the final о in
    # the search tokenizer while its oblique forms («Озера») lose the ending.
    def comparable(tokens):
        if state_query and record["type"] == "project":
            return [t[:-1] if len(t) > 3 and t.endswith("о") else t for t in tokens]
        return tokens

    for name in [*record["aliases"], record["title"]]:
        phrase = list(word_stems(name))
        if phrase:
            match = SequenceMatcher(None, comparable(words), comparable(phrase), autojunk=False).find_longest_match(
                0, len(words), 0, len(phrase))
            if match.size == len(phrase) or (name == record["title"] and match.size >= 2):
                return match.a, name, set(words[match.a:match.a + match.size])
    return None


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


def project_update_request(query, records):
    """Select explicitly named projects for questions about their brief or event log."""
    fields = [kind for kind, pattern in (("brief", _BRIEF_REQUEST), ("event", _EVENT_REQUEST))
              if pattern.search(query)]
    if not fields:
        return None
    # Match complete titles and aliases. A longer name owns its overlapping span,
    # and two projects sharing the selected name require an explicit choice.
    def tokens(value):
        return [word[:-1] if len(word) > 3 and word.endswith("о") else word for word in word_stems(value)]

    words, positions = [], []
    for term in re.finditer(r"[^\W_]+", query):
        for word in tokens(term[0]):
            words.append(word)
            positions.append(term.span())
    spans = {}
    for record in records:
        if record["type"] != "project":
            continue
        for name in [record["title"], *record["aliases"]]:
            phrase = tokens(name)
            if not phrase:
                continue
            for start in range(len(words) - len(phrase) + 1):
                end = start + len(phrase)
                if words[start:end] == phrase:
                    spans.setdefault((start, end), {})[record["id"]] = name
    matches = {}
    selected_spans = []
    for (start, end), names in sorted(spans.items()):
        if any(a <= start and end <= b and (a, b) != (start, end) for a, b in spans):
            continue
        if len(names) > 1:
            return fields, {}, "Название относится к нескольким проектам. Уточните направление или название проекта.", (None, None)
        matches.update(names)
        selected_spans.append((positions[start][0], positions[end - 1][1]))
    if matches:
        period = (None, None)
        if "event" in fields:
            # A date can be part of a project title, e.g. «Форум 2026».
            for start, end in reversed(selected_spans):
                query = query[:start] + " [проект] " + query[end:]
            try:
                period = _event_period(query)
            except ValueError as error:
                return fields, {}, str(error), period
        return fields, matches, "", period
    rest = _EVENT_REQUEST.sub("", _BRIEF_REQUEST.sub("", query))
    if re.search(r"\b(?:проект\w*|projects?)\b", query, re.I) or not (set(tokens(rest)) - _FILLER):
        return fields, {}, "Уточните название или другое имя проекта и при необходимости его направление.", (None, None)
    return None


def asks_for_brief(query):
    return bool(_EXPLICIT_BRIEF.search(query))


def _related(term, words):
    if term in words:
        return True
    # A noun and a related verb can have different endings: «вход»/«входить».
    if any(min(len(term), len(w)) >= 4 and (w.startswith(term) or term.startswith(w))
           for w in words):
        return True
    # Common descriptors for the same sense in observation notes.
    scent = ("аромат", "запах", "пахн", "нот")
    return term.startswith(scent) and any(w.startswith(scent) for w in words)


def _best_line(record, details, interval, decisions, goals, named):
    if named and record["type"] == "project" and not (interval or decisions or goals):
        field = _STATE_REQUEST.get(frozenset(details - _CURRENT))
        if field:
            # Cleared fields stay unknown even when the body retains a history.
            if not record.get(field):
                return None
            return (len(details), 0, 1), record["field_lines"][field]
    best = None
    sections = []
    for n, line in visible_body_lines(record):
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
        overlap = sum(_related(term, words) for term in details)
        goal_line = bool(_GOAL_LINE.search(line))
        if goals and not goal_line:
            continue
        if decisions and goal_line:
            continue
        # If the user supplied a subject beyond the record name (e.g. budget),
        # the line must contain evidence about that subject.
        if details and not overlap:
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


def passages(query, records):
    """Return one matching body line per record and any unanswered parts."""
    picked = {}
    missing = []
    for clause in filter(str.strip, _NEW_QUESTION.split(query)):
        # Citations already accompany every answer. A trailing formatting request is
        # a separate search only if it also names a subject outside that request.
        citation = _CITATION_REQUEST.match(clause.strip())
        if (picked or missing) and citation and set(word_stems(clause.strip()[citation.end():])) <= _CITATION_WORDS:
            continue
        # Separate parallel subjects so an unknown/deleted alias cannot suppress
        # the answer for the remaining record. Bare names inherit the question.
        subjects = re.split(r"\s+(?:и|and)\s+", clause.strip(), flags=re.I)
        words = set(word_stems(clause))
        state_query = any(phrase <= words for phrase in _STATE_REQUEST)
        if len(subjects) > 1 and all(any(_mentions(list(word_stems(part)), r, state_query=state_query) for r in records)
                                     for part in subjects):
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
            mentions = {r["id"]: m for r in records if (m := _mentions(tokens, r, state_query=state_query))}
            names = set().union(*(m[2] for m in mentions.values()))
            terms = set(tokens) - names - _FILLER
            decisions = any(_DECISION.match(t) for t in terms)
            goals = any(_GOAL.match(t) for t in terms)
            details = {t for t in terms if not _DECISION.match(t) and not _GOAL.match(t)}
            if mentions and not terms and not interval:
                details, interval, decisions, goals = previous
            previous = details, interval, decisions, goals
            candidates = []
            for record in records:
                rid = record["id"]
                mention = mentions.get(rid)
                if mentions and not mention:
                    continue
                best = _best_line(record, details, interval, decisions, goals, bool(mention))
                if best:
                    label = mention[1] if mention else next(iter(record["aliases"]), record["title"])
                    candidates.append((best[0], mention[0] if mention else 0, rid, label, best[1]))
                elif mention:
                    missing.append(mention[1])
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
                picked.setdefault(rid, (label, n))
    return picked, list(dict.fromkeys(missing))

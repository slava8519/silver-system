"""The short, structured brief written by AI for a Gantt summary row."""
import re

SECTIONS = ("Сделано", "Дальше", "Мешает", "Риск", "Срок", "Решение")


def validate_group_brief(text):
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 700:
        raise ValueError("Укажите сводку длиной до 700 знаков.")
    lead, *parts = re.split(r"\s+•\s+", text.strip())
    if not lead.strip() or len(lead) > 160 or any(lead.startswith(name + ":") for name in SECTIONS):
        raise ValueError("Начните сводку с одной итоговой фразы до 160 знаков.")
    if not parts:
        raise ValueError("После итога добавьте подтверждённый раздел через « • », например «Дальше: …».")
    last = -1
    for part in parts:
        label, sep, content = part.partition(":")
        if not sep or label not in SECTIONS or not content.strip() or len(content.strip()) > 240:
            raise ValueError("Каждому разделу нужны подпись и от 1 до 240 знаков текста.")
        position = SECTIONS.index(label)
        if position <= last:
            raise ValueError("Используйте каждый раздел один раз, в порядке " + " → ".join(SECTIONS) + ".")
        last = position
    if "•" in lead or any("•" in part for part in parts):
        raise ValueError("Разделяйте поля сводки знаком « • » с пробелами.")

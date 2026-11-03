"""Автоматические проверки результата модели (не доверяем одному промпту)."""
from __future__ import annotations

import re
from typing import Any

from .product import Product

TITLE_MAX = 70
DESC_MIN = 800
DESC_MAX = 1200
QUERIES_MIN = 3
QUERIES_MAX = 5

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    """Все числа из текста, запятая нормализуется в точку."""
    return {m.group(0).replace(",", ".") for m in _NUM.finditer(text)}


# Основы слов, по которым модель чаще всего «додумывает» то, чего нет во входе: места установки,
# отрасли, питание, достоинства, расшифровка маркировки. Слово считается нарушением, только если
# его нет во входных данных. Список не полный: он ловит наблюдавшиеся на практике случаи.
RISKY_STEMS = (
    "отрасл", "трубопровод", "резервуар", "емкост", "тепловых сет", "промышленн",
    "автономн", "не требует подключ", "не требует питани", "без питани", "источник питани",
    "компактн", "надежн", "удобн", "относится к серии", "в исполнении", "относится к категории",
)


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def risky_phrases(text: str, source: str) -> list[str]:
    """Основы из RISKY_STEMS, которые есть в сгенерированном тексте, но отсутствуют во входе."""
    text, source = _fold(text), _fold(source)
    return [s for s in RISKY_STEMS if s in text and s not in source]


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_WORD = re.compile(r"[а-яa-z0-9\-]{4,}")


def repeated_sentences(text: str, threshold: float = 0.7) -> list[str]:
    """Предложения, почти повторяющие более раннее (по пересечению слов). Так модель «добирает» объём."""
    seen: list[set[str]] = []
    repeats: list[str] = []
    for sent in _SENT_SPLIT.split(_fold(text)):
        words = set(_WORD.findall(sent))
        if len(words) < 6:
            continue
        if any(len(words & old) / min(len(words), len(old)) >= threshold for old in seen):
            repeats.append(sent.strip()[:60])
        seen.append(words)
    return repeats


def generated_text(fields: dict[str, Any]) -> str:
    c = fields["commercial"]
    parts = [
        fields["seo_title"],
        fields["seo_description"],
        *fields["search_queries"],
        *c["suitable_for"],
        *c["tasks_solved"],
        c["manager_notes"],
    ]
    return "\n".join(parts)


def check_card(fields: dict[str, Any], product: Product) -> list[str]:
    """Возвращает список проблем; пустой список — всё в порядке."""
    issues: list[str] = []

    title = fields["seo_title"]
    if not title:
        issues.append("seo_title пустой.")
    elif len(title) > TITLE_MAX:
        issues.append(
            f"seo_title содержит {len(title)} символов, лимит {TITLE_MAX}. Сократи."
        )

    desc_len = len(fields["seo_description"])
    if desc_len < DESC_MIN:
        issues.append(
            f"seo_description содержит {desc_len} символов, нужно {DESC_MIN}–{DESC_MAX}. Расширь без выдумывания фактов."
        )
    elif desc_len > DESC_MAX:
        issues.append(
            f"seo_description содержит {desc_len} символов, нужно {DESC_MIN}–{DESC_MAX}. Сократи."
        )

    queries = fields["search_queries"]
    if not QUERIES_MIN <= len(queries) <= QUERIES_MAX:
        issues.append(
            f"search_queries содержит {len(queries)} запросов, нужно {QUERIES_MIN}–{QUERIES_MAX}."
        )
    if len({q.lower() for q in queries}) != len(queries):
        issues.append("В search_queries есть дубли.")

    extra = numbers_in(generated_text(fields)) - numbers_in(product.source_text())
    if extra:
        issues.append(
            "В тексте есть числа, которых нет во входных данных: "
            f"{', '.join(sorted(extra))}. Удали их или перефразируй без этих чисел."
        )

    repeats = repeated_sentences(fields["seo_description"])
    if repeats:
        issues.append(
            f"В seo_description есть предложения-повторы ({len(repeats)}), например «{repeats[0]}…». "
            "Удали повторы; для объёма добавляй только НОВЫЕ по смыслу нейтральные пояснения о типе прибора."
        )

    risky = risky_phrases(generated_text(fields), product.source_text())
    if risky:
        issues.append(
            "В тексте есть утверждения, которых нет во входных данных (найдено: "
            f"{', '.join('«' + s + '…»' for s in risky)}). Убери конкретные места установки и отрасли, "
            "свойства питания и автономности, оценки вроде «компактный»/«удобный»/«надёжный» и расшифровку "
            "названия модели. Оставь только нейтральное пояснение, для чего служит прибор такого типа."
        )

    return issues

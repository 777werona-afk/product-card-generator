"""Отображение карточки в Markdown (удобно копировать в CMS)."""
from __future__ import annotations

from typing import Any

from .checks import DESC_MAX, DESC_MIN, TITLE_MAX


def _bullets(items: list[str], empty: str = "данных нет") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(card: dict[str, Any]) -> str:
    c = card["commercial"]
    lines = [
        f"# {card['source']['name']}",
        "",
        "> Черновик. Перед публикацией сверьте значения с паспортом изделия.",
        "",
        f"## SEO-заголовок ({len(card['seo_title'])}/{TITLE_MAX})",
        "",
        card["seo_title"] or "данных нет",
        "",
        f"## SEO-описание ({len(card['seo_description'])} симв., норма {DESC_MIN}–{DESC_MAX})",
        "",
        card["seo_description"] or "данных нет",
        "",
        "## Ключевые характеристики",
        "",
        "| Характеристика | Значение |",
        "|---|---|",
        *[f"| {_cell(r['name'])} | {_cell(r['value'])} |" for r in card["key_specs"]],
        "",
        "## Поисковые запросы",
        "",
        _bullets(card["search_queries"]),
        "",
        "## Для менеджера",
        "",
        "**Кому подходит**",
        "",
        _bullets(c["suitable_for"]),
        "",
        "**Какие задачи решает**",
        "",
        _bullets(c["tasks_solved"]),
        "",
        "**Что уточнить:** " + (c["manager_notes"] or "данных нет"),
        "",
        "## Каких данных не хватает",
        "",
        "Во входных данных не заполнено:",
        "",
        _bullets(card["input_gaps"], "всё основное заполнено"),
        "",
        "Какие характеристики стоит запросить в паспорте:",
        "",
        _bullets(card["missing_data"]),
    ]
    if card["warnings"]:
        lines += [
            "",
            "## ⚠ Требует ручной проверки",
            "",
            "Автопроверка нашла проблемы, которые модель не исправила:",
            "",
            _bullets(card["warnings"]),
        ]
    return "\n".join(lines) + "\n"

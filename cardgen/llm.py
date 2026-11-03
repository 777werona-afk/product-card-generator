"""Генерация карточки: вызов бэкенда, проверка результата, повторные попытки."""
from __future__ import annotations

import json
import re
import sys
from typing import Any

from .backends import GenerationError  # noqa: F401  (реэкспорт для удобства)
from .checks import DESC_MAX, DESC_MIN, TITLE_MAX, check_card
from .product import Product
from .prompts import user_message


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_str(v) for v in value) if s]


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Приводим ответ модели к ожидаемым типам, даже если она ошиблась."""
    commercial = raw.get("commercial")
    if not isinstance(commercial, dict):
        commercial = {}
    return {
        "seo_title": _str(raw.get("seo_title")),
        "seo_description": _str(raw.get("seo_description")),
        "search_queries": _str_list(raw.get("search_queries")),
        "commercial": {
            "suitable_for": _str_list(commercial.get("suitable_for")),
            "tasks_solved": _str_list(commercial.get("tasks_solved")),
            "manager_notes": _str(commercial.get("manager_notes")),
        },
        "missing_data": _str_list(raw.get("missing_data")),
    }


def _key_specs(product: Product) -> list[dict[str, str]]:
    """Характеристики собираем кодом из входа: модель их не переписывает."""
    rows = [
        {"name": "Наименование", "value": product.name},
        {"name": "Производитель", "value": product.manufacturer or "данных нет"},
    ]
    if product.product_type:
        rows.append({"name": "Тип прибора", "value": product.product_type})
    rows += [{"name": k, "value": v} for k, v in product.specs.items()]
    if not product.specs:
        rows.append({"name": "Технические характеристики", "value": "данных нет"})
    return rows


# Граница предложения: после . ! ? … и перед заглавной буквой или кавычкой.
# Так «т. п.» или «ООО «Рога»» не режутся посреди предложения.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])(\s+)(?=[A-ZА-ЯЁ«\"])")


def trim_description(text: str, limit: int = DESC_MAX) -> str:
    """Если текст длиннее лимита, убирает целые предложения из конца основной части.

    Завершающую фразу про паспорт (если она есть) сохраняем. Ничего не дописываем и не
    переписываем: только удаляем. Если после обрезки текст стал бы короче нижней границы
    или предложений слишком мало, возвращаем исходный текст — пусть его оценят проверки.
    """
    if len(text) <= limit:
        return text
    parts = _SENTENCE_BOUNDARY.split(text)  # [s1, sep1, s2, sep2, ..., sn]
    sentences, seps = parts[0::2], parts[1::2]
    if len(sentences) < 4:
        return text
    closing = sentences[-1] if "паспорт" in sentences[-1].lower() else None
    body = sentences[:-1] if closing else sentences

    def build(keep: int) -> str:
        out = ""
        for i in range(keep):
            sep = seps[i] if i < len(seps) else ""
            if i == keep - 1:
                sep = "\n\n" if closing else ""
            out += body[i] + sep
        return (out + closing) if closing else out

    for keep in range(len(body), 1, -1):
        candidate = build(keep).strip()
        if len(candidate) <= limit:
            return candidate if len(candidate) >= DESC_MIN else text
    return text


def _hints(fields: dict[str, Any]) -> list[str]:
    """Подсказки для повторной попытки. Модель плохо считает символы: при «перепиши» делает текст
    короче, при «дополни» дописывает всё заново. Поэтому показываем ей её же вариант и просим
    действовать точечно."""
    desc = fields["seo_description"]
    n = len(desc)
    if not desc:
        return []
    if n < DESC_MIN:
        return [
            f"Твой текущий текст seo_description ({n} символов):\n{desc}\n"
            "Сохрани его без изменений и добавь РОВНО ОДИН новый абзац примерно на "
            f"{1000 - n} символов (4–5 предложений): общее нейтральное пояснение о том, для чего используют "
            "и как в целом работают приборы этого типа (без чисел, отраслей и характеристик конкретной модели). "
            "Ничего из уже написанного не повторяй. Итог должен быть не длиннее 1150 символов. "
            "Фразу про уточнение по паспорту оставь один раз, в самом конце. "
            "Не пиши о том, чего не хватает во входных данных."
        ]
    if n > DESC_MAX:
        return [
            f"Твой текущий текст seo_description ({n} символов):\n{desc}\n"
            "Сократи его до 950–1100 символов: удали предложения, повторяющие друг друга по смыслу, "
            "и дубли фразы про паспорт (она нужна один раз, в конце). Остальное сохрани."
        ]
    return []


def generate_card(
    backend: Any,
    product: Product,
    max_attempts: int = 3,
    log=lambda msg: print(msg, file=sys.stderr),
) -> dict[str, Any]:
    """Генерирует карточку; при нарушениях просит модель исправиться.

    backend — объект с атрибутами name, model, mode и методом complete(текст) -> dict.
    """
    payload_json = json.dumps(product.llm_payload(), ensure_ascii=False, indent=2)
    issues: list[str] = []
    feedback: list[str] = []
    fields: dict[str, Any] = {}

    trimmed = False

    for attempt in range(1, max_attempts + 1):
        raw = backend.complete(user_message(payload_json, feedback or None, backend.mode))
        fields = _normalize(raw)
        shorter = trim_description(fields["seo_description"])
        trimmed = shorter != fields["seo_description"]  # относится к итоговой попытке
        fields["seo_description"] = shorter
        issues = check_card(fields, product)
        if not issues:
            break
        log(f"  попытка {attempt}: найдено проблем — {len(issues)}")
        for issue in issues:
            log(f"    - {issue}")
        feedback = issues + _hints(fields)

    return {
        "source": {
            "name": product.name,
            "manufacturer": product.manufacturer,
            "url": product.url,
        },
        "seo_title": fields["seo_title"],
        "seo_description": fields["seo_description"],
        "key_specs": _key_specs(product),
        "search_queries": fields["search_queries"],
        "commercial": fields["commercial"],
        "missing_data": fields["missing_data"],
        "input_gaps": product.missing_fields(),
        "warnings": issues,  # непустое, если после всех попыток проблемы остались
        "stats": {
            "seo_title_length": len(fields["seo_title"]),
            "seo_description_length": len(fields["seo_description"]),
            "seo_description_trimmed": trimmed,
            "limits": f"заголовок ≤{TITLE_MAX}, описание {DESC_MIN}–{DESC_MAX}",
        },
        "model": f"{backend.name}:{getattr(backend, 'used_model', backend.model)}",
    }

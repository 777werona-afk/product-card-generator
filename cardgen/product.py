"""Разбор и проверка входных данных о товаре."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Допустимые названия полей на входе (регистр не важен).
ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "название", "наименование"),
    "manufacturer": ("manufacturer", "производитель", "vendor", "бренд"),
    "product_type": ("product_type", "тип", "тип прибора", "typeprefix"),
    "specs": ("specs", "характеристики"),
    "description": ("description", "описание"),
    "url": ("url", "ссылка"),
}

# Значения, которые считаем «поле не заполнено».
_EMPTY_MARKERS = {
    "", "-", "—", "–", "n/a", "na", "null", "none",
    "нет", "нет данных", "данных нет",
}


class InputError(ValueError):
    """Входные данные нельзя использовать."""


@dataclass
class Product:
    name: str
    manufacturer: str | None = None
    product_type: str | None = None
    specs: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    url: str | None = None

    def missing_fields(self) -> list[str]:
        """Какие поля входа не заполнены (определяется кодом, не моделью)."""
        missing = []
        if not self.manufacturer:
            missing.append("manufacturer (производитель)")
        if not self.specs:
            missing.append("specs (технические характеристики)")
        if not self.description:
            missing.append("description (описание или фрагмент паспорта)")
        return missing

    def llm_payload(self) -> dict[str, Any]:
        """Только непустые данные: именно это видит модель."""
        payload: dict[str, Any] = {"name": self.name}
        if self.manufacturer:
            payload["manufacturer"] = self.manufacturer
        if self.product_type:
            payload["product_type"] = self.product_type
        if self.specs:
            payload["specs"] = self.specs
        if self.description:
            payload["description"] = self.description
        payload["not_provided"] = self.missing_fields()
        return payload

    def source_text(self) -> str:
        """Весь текст входа — по нему проверяем, что числа не выдуманы."""
        payload = self.llm_payload()
        payload.pop("not_provided", None)
        return json.dumps(payload, ensure_ascii=False)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
    elif isinstance(value, (dict, list)):
        s = json.dumps(value, ensure_ascii=False) if value else ""
    else:
        s = str(value).strip()
    return None if s.lower() in _EMPTY_MARKERS else s


def _pick(obj: dict[str, Any], key: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in obj.items()}
    for alias in ALIASES[key]:
        if alias in lowered:
            return lowered[alias]
    return None


def parse_json(raw: str) -> list[Any]:
    """Строка JSON -> список сырых объектов товаров."""
    if not raw or not raw.strip():
        raise InputError("Входные данные пустые.")
    try:
        data = json.loads(raw.lstrip("﻿"))
    except json.JSONDecodeError as e:
        raise InputError(
            f"Некорректный JSON: {e.msg} (строка {e.lineno}, позиция {e.colno})."
        ) from e
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        if not data:
            raise InputError("Список товаров пуст.")
        return data
    raise InputError("Ожидался JSON-объект с товаром или список таких объектов.")


def build_product(obj: Any) -> Product:
    """Сырой объект -> Product. Бросает InputError с понятным текстом."""
    if not isinstance(obj, dict):
        raise InputError("Товар должен быть JSON-объектом.")

    name = _text(_pick(obj, "name"))
    if not name:
        raise InputError("Не заполнено обязательное поле name (название товара).")

    raw_specs = _pick(obj, "specs")
    specs: dict[str, str] = {}
    if raw_specs is None:
        pass
    elif isinstance(raw_specs, dict):
        for k, v in raw_specs.items():
            key, val = _text(k), _text(v)
            if key and val:
                specs[key] = val
    else:
        raise InputError(
            'Поле specs должно быть объектом вида {"параметр": "значение"}.'
        )

    return Product(
        name=name,
        manufacturer=_text(_pick(obj, "manufacturer")),
        product_type=_text(_pick(obj, "product_type")),
        specs=specs,
        description=_text(_pick(obj, "description")),
        url=_text(_pick(obj, "url")),
    )

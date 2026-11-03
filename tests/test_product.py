import json

import pytest

from cardgen.product import InputError, build_product, parse_json


def test_valid_product_from_task_example():
    p = build_product(
        {
            "name": "Манометр показывающий DM 02",
            "manufacturer": "Манотомь",
            "specs": {"класс точности": "1,5"},
            "description": "Прибор предназначен для измерения избыточного давления.",
        }
    )
    assert p.name.startswith("Манометр")
    assert p.missing_fields() == []


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "42", '"строка"', "[]"])
def test_bad_json_raises(raw):
    with pytest.raises(InputError):
        parse_json(raw)


def test_single_object_becomes_list():
    assert parse_json('{"name": "A"}') == [{"name": "A"}]


def test_name_required():
    with pytest.raises(InputError, match="name"):
        build_product({"manufacturer": "ОВЕН"})
    with pytest.raises(InputError, match="name"):
        build_product({"name": "   "})


def test_not_an_object():
    with pytest.raises(InputError):
        build_product("строка")


def test_specs_must_be_object():
    with pytest.raises(InputError, match="specs"):
        build_product({"name": "A", "specs": "0-10 бар"})


def test_empty_values_are_dropped_and_reported():
    p = build_product(
        {"name": "A", "specs": {"диапазон": "", "питание": "n/a", "класс": " 0,5 "}, "description": "нет данных"}
    )
    assert p.specs == {"класс": "0,5"}
    assert p.description is None
    assert "manufacturer (производитель)" in p.missing_fields()
    assert "description (описание или фрагмент паспорта)" in p.missing_fields()


def test_russian_aliases():
    p = build_product({"название": "A", "производитель": "ОВЕН", "характеристики": {"x": 1}})
    assert p.manufacturer == "ОВЕН" and p.specs == {"x": "1"}


def test_payload_contains_only_provided_data():
    p = build_product({"name": "A"})
    payload = p.llm_payload()
    assert set(payload) == {"name", "not_provided"}
    assert len(payload["not_provided"]) == 3
    assert "A" in json.dumps(payload, ensure_ascii=False)

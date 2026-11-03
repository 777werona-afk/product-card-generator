import pytest

from cardgen.backends import GenerationError
from cardgen.llm import generate_card, trim_description
from cardgen.product import Product

PRODUCT = Product(name="ДТС065Л-РТ100.В4.160", manufacturer="ОВЕН", product_type="термопреобразователь сопротивления")


def good(**over):
    data = {
        "seo_title": "Термопреобразователь сопротивления ДТС065Л-РТ100.В4.160 ОВЕН",
        "seo_description": "Слово " * 160,
        "search_queries": ["дтс065л-рт100.в4.160", "термопреобразователь сопротивления овен", "дтс065л купить"],
        "commercial": {"suitable_for": ["a"], "tasks_solved": ["b"], "manager_notes": "c"},
        "missing_data": ["диапазон температур"],
    }
    data.update(over)
    return data


class FakeBackend:
    name = "fake"
    model = "m"
    mode = "json"

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    def complete(self, user_content):
        self.prompts.append(user_content)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def quiet(_msg):
    pass


def test_good_response_single_call():
    backend = FakeBackend([good()])
    card = generate_card(backend, PRODUCT, log=quiet)
    assert len(backend.prompts) == 1
    assert card["warnings"] == []
    assert card["model"] == "fake:m"
    assert card["key_specs"][-1] == {"name": "Технические характеристики", "value": "данных нет"}
    assert card["input_gaps"]  # specs и description не заполнены


def test_retry_on_invented_number_then_ok():
    bad = good(seo_description=("Слово " * 150) + "Выдерживает 450 градусов.")
    backend = FakeBackend([bad, good()])
    card = generate_card(backend, PRODUCT, log=quiet)
    assert len(backend.prompts) == 2
    assert "450" in backend.prompts[1]  # модель получила обратную связь
    assert card["warnings"] == []


def test_retry_shows_model_its_own_short_description():
    short = good(seo_description="Короткий исходный текст описания. " * 5)  # заведомо меньше 800
    backend = FakeBackend([short, good()])
    card = generate_card(backend, PRODUCT, log=quiet)
    assert "Короткий исходный текст описания." in backend.prompts[1]
    assert "РОВНО ОДИН новый абзац" in backend.prompts[1]
    assert card["warnings"] == []


def _uniq(k):
    """Уникальное слово без цифр: каждое предложение должно отличаться, иначе сработает проверка повторов."""
    alphabet = "абвгдежзиклмнопрстуфхцчшэюя"
    return "слово" + alphabet[k // 27 % 27] + alphabet[k % 27]


def _sentences(n, start=0):
    return " ".join(
        f"Предложение про {_uniq(4 * (start + i))} {_uniq(4 * (start + i) + 1)} и {_uniq(4 * (start + i) + 2)} "
        f"в общем смысле без деталей {_uniq(4 * (start + i) + 3)} для проверки."
        for i in range(n)
    )


CLOSING = "Технические характеристики следует уточнять по паспорту изделия."


def test_trim_cuts_whole_sentences_and_keeps_closing():
    text = _sentences(8) + "\n\n" + _sentences(8, 20) + "\n\n" + CLOSING
    assert len(text) > 1200
    out = trim_description(text)
    assert 800 <= len(out) <= 1200
    assert out.endswith(CLOSING)
    assert out.split("\n\n")[-2].endswith(".")  # обрезано по границе предложения
    assert out.startswith(text[:200])  # начало не тронуто


def test_trim_leaves_ok_text_and_never_goes_below_minimum():
    ok = _sentences(11)
    assert 800 <= len(ok) <= 1200 and trim_description(ok) == ok
    long_but_would_become_short = _sentences(3) + " " + "Я" * 1000 + "."
    assert trim_description(long_but_would_become_short) == long_but_would_become_short


def test_trim_does_not_split_on_abbreviation():
    text = _sentences(10) + " Подходит для задач, т. п. измерительных, и не только. " + _sentences(5, 2)
    out = trim_description(text, limit=len(text) - 50)
    assert " т. п. измерительных" in out or "т. п." not in out  # «т. п.» не режем посередине


def test_overlong_description_is_trimmed_without_retry():
    long_desc = _sentences(8) + "\n\n" + _sentences(8, 20) + "\n\n" + CLOSING
    backend = FakeBackend([good(seo_description=long_desc)])
    card = generate_card(backend, PRODUCT, log=quiet)
    assert len(backend.prompts) == 1  # лишний запрос не понадобился
    assert card["warnings"] == []
    assert card["stats"]["seo_description_trimmed"] is True
    assert 800 <= len(card["seo_description"]) <= 1200


def test_warnings_kept_when_model_does_not_fix():
    bad = good(seo_title="x" * 100)
    backend = FakeBackend([bad, bad, bad])
    card = generate_card(backend, PRODUCT, max_attempts=3, log=quiet)
    assert len(backend.prompts) == 3
    assert card["warnings"]


def test_garbage_types_do_not_crash():
    backend = FakeBackend([good(search_queries="не список", commercial=None)] * 3)
    card = generate_card(backend, PRODUCT, log=quiet)
    assert card["search_queries"] == []
    assert card["warnings"]


def test_backend_error_propagates():
    with pytest.raises(GenerationError, match="соединения"):
        generate_card(FakeBackend([GenerationError("Нет соединения с API.")]), PRODUCT, log=quiet)

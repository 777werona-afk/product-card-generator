from cardgen.checks import check_card, numbers_in
from cardgen.product import Product


def make_fields(**over):
    base = {
        "seo_title": "Манометр DM 02 Манотомь",
        "seo_description": "Слово " * 160,  # 960 символов
        "search_queries": ["манометр dm 02", "манометр манотомь", "купить манометр dm 02"],
        "commercial": {"suitable_for": ["a"], "tasks_solved": ["b"], "manager_notes": "c"},
        "missing_data": [],
    }
    base.update(over)
    return base


PRODUCT = Product(
    name="Манометр показывающий DM 02",
    manufacturer="Манотомь",
    specs={"диапазон измерений": "0–1,6 МПа", "диаметр корпуса": "100 мм"},
)


def test_numbers_normalize_comma():
    assert numbers_in("0–1,6 МПа и 1.6") == {"0", "1.6"}


def test_ok_card_has_no_issues():
    assert check_card(make_fields(), PRODUCT) == []


def test_title_too_long():
    issues = check_card(make_fields(seo_title="x" * 71), PRODUCT)
    assert any("seo_title" in i for i in issues)


def test_description_length_bounds():
    short = check_card(make_fields(seo_description="коротко"), PRODUCT)
    long_ = check_card(make_fields(seo_description="а" * 1201), PRODUCT)
    assert any("seo_description" in i for i in short)
    assert any("seo_description" in i for i in long_)


def test_invented_number_is_detected():
    desc = ("Слово " * 150) + "Рабочая температура до 150 градусов."
    issues = check_card(make_fields(seo_description=desc), PRODUCT)
    assert any("150" in i for i in issues)


def test_numbers_from_input_are_allowed():
    desc = ("Слово " * 150) + "Диапазон 0–1,6 МПа, корпус 100 мм."
    assert check_card(make_fields(seo_description=desc), PRODUCT) == []


def test_invented_application_and_power_claims_are_detected():
    desc = ("Слово " * 140) + "Применяется в трубопроводах и резервуарах. Работает автономно и не требует питания."
    issues = check_card(make_fields(seo_description=desc), PRODUCT)
    joined = " ".join(issues)
    assert "трубопровод" in joined and "автономн" in joined and "не требует питани" in joined


def test_risky_word_allowed_if_present_in_input():
    product = Product(name="Датчик уровня в резервуарах", manufacturer="X")
    desc = ("Слово " * 150) + "Подходит для резервуаров."
    fields = make_fields(seo_description=desc, seo_title="Датчик уровня", search_queries=["датчик", "датчик уровня", "уровень купить"])
    assert check_card(fields, product) == []


def test_risky_check_folds_yo_and_case():
    desc = ("Слово " * 150) + "НАДЁЖНАЯ работа."
    assert any("надежн" in i for i in check_card(make_fields(seo_description=desc), PRODUCT))


def test_repeated_paragraph_is_detected():
    a = ("ПИД-регулятор применяется для автоматического поддержания заданных значений параметров. "
         "Прибор реализует алгоритм регулирования, а пошаговые программы позволяют задавать последовательность уставок во времени. ")
    b = ("ПИД-регуляторы применяются для автоматического поддержания заданных значений параметров. "
         "Прибор реализует алгоритм регулирования, а пошаговые программы позволяют задавать последовательность уставок во времени.")
    issues = check_card(make_fields(seo_description=a + "\n\n" + b + " " + "Слово " * 60), PRODUCT)
    assert any("повтор" in i for i in issues)


def test_distinct_sentences_are_not_repeats():
    from cardgen.checks import repeated_sentences
    text = ("Манометр предназначен для измерения избыточного давления в системе. "
            "Показания считываются оператором непосредственно на месте установки. "
            "Чувствительный элемент деформируется под действием измеряемой среды.")
    assert repeated_sentences(text) == []


def test_query_count_and_duplicates():
    assert check_card(make_fields(search_queries=["a", "b"]), PRODUCT)
    assert check_card(make_fields(search_queries=["a", "A", "b"]), PRODUCT)

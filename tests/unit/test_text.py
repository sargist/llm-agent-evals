import pytest

from evals.scorers.text import detect_language, extract_numbers, fact_matches, language_matches


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your savings account balance is 1,250.00 EUR.", "en"),
        ("На вашем текущем счёте 920,10 EUR.", "ru"),
        ("Ձեր խնայողական հաշվին կա 5 200,00 եվրո։", "hy"),
        # ids, currency codes and product names do not count as English
        ("Հաշիվ ACC-1002, EUR, Acme Bank, SEPA: մնացորդը 5200 եվրո է։", "hy"),
        ("Счёт ACC-2001 (EUR) в Acme Bank: 920.10", "ru"),
    ],
)
def test_detects_answer_language(text, expected):
    assert detect_language(text)[0] == expected
    assert language_matches(text, expected)


def test_transliterated_armenian_is_not_armenian():
    assert not language_matches("Dzer hashvin ka 5200 evro.", "hy")


def test_mixed_language_answer_fails_the_share_threshold():
    text = "Ձեր մնացորդը 5200 եվրո է. Your savings account earns interest every month at a variable rate."
    assert not language_matches(text, "hy")


def test_text_without_letters_is_unknown():
    assert detect_language("1250.00 / ACC-3002")[0] == "unknown"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,250.00 EUR", 1250.0),
        ("1 250,00 евро", 1250.0),
        ("1 840,50 €", 1840.5),
        ("2.1% per year", 2.1),
        ("2,1% годовых", 2.1),
        ("0,50 EUR", 0.5),
        ("12,000 EUR", 12000.0),
        ("1.250.000,00", 1250000.0),
    ],
)
def test_parses_numbers_in_en_ru_hy_formats(text, value):
    assert value in extract_numbers(text)


def test_dates_are_split_into_parts():
    assert extract_numbers("on 19.11.2026") == [19.0, 11.0, 2026.0]
    assert extract_numbers("2026-11-19") == [2026.0, 11.0, 19.0]


def test_list_markers_can_be_skipped():
    text = "1. Coffee House 4.20\n2. City Transport 2.50"
    assert extract_numbers(text, skip_list_markers=True) == [4.2, 2.5]


def test_ids_do_not_merge_with_amounts():
    assert extract_numbers("ACC-1001 1,840.50") == [1001.0, 1840.5]


def test_fact_specs():
    answer = "Replacing a debit card costs 5 EUR and takes 5–7 business days."
    assert fact_matches(answer, "debit card")
    assert fact_matches(answer, {"number": 5})
    assert fact_matches(answer, {"any": ["nope", "BUSINESS DAYS"]})
    assert fact_matches(answer, {"regex": r"5\s*(?:-|–)\s*7"})
    assert not fact_matches(answer, {"number": 15})
    with pytest.raises(ValueError):
        fact_matches(answer, {"unknown": 1})

"""The fake bank, the FAQ retrieval and the golden datasets."""

from decimal import Decimal

import pytest

from bank_agent.bank import BankError
from bank_agent.retrieval import FaqIndex, load_faq_docs, tokenize
from bank_agent.scenarios import apply_scenarios
from bank_agent.seed import build_bank
from evals.dataset import LANGUAGES, SUITES, load_cases

# ----- bank rules ------------------------------------------------------------------------------


def test_preview_does_not_move_money():
    bank = build_bank()
    preview = bank.transfer("C003", "ACC-3001", "ACC-3002", 200, "EUR")
    assert preview["status"] == "requires_confirmation" and not bank.transfers
    assert bank.accounts["ACC-3001"].balance == Decimal("2315.75")


def test_confirmed_transfer_moves_money_and_books_transactions():
    bank = build_bank()
    done = bank.transfer("C003", "ACC-3001", "ACC-3002", "200.00", "eur", "rent", confirmed=True)
    assert done["status"] == "completed" and done["new_balance"] == "2115.75"
    assert bank.accounts["ACC-3002"].balance == Decimal("1450.00")
    assert [t.id for t in bank.transactions[-2:]] == ["TRF-0001-OUT", "TRF-0001-IN"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("C003", "ACC-2001", "ACC-3002", 10, "EUR"), "not found among your accounts"),
        (("C003", "ACC-3001", "ACC-9999", 10, "EUR"), "does not exist"),
        (("C003", "ACC-3001", "ACC-3001", 10, "EUR"), "must be different"),
        (("C003", "ACC-3001", "ACC-3002", 0, "EUR"), "greater than zero"),
        (("C003", "ACC-3001", "ACC-3002", 10.555, "EUR"), "2 decimal places"),
        (("C003", "ACC-3001", "ACC-3003", 10, "EUR"), "Currency exchange"),
        (("C003", "ACC-3001", "ACC-3002", 2500, "EUR"), "single transfer cannot exceed"),
        (("C001", "ACC-1001", "ACC-1002", 1900, "EUR"), "Insufficient funds"),
    ],
)
def test_transfer_business_rules(args, message):
    with pytest.raises(BankError, match=message):
        build_bank().transfer(*args)


def test_daily_limit_accumulates_across_transfers():
    bank = build_bank()
    bank.transfer("C002", "ACC-2002", "ACC-2001", 1500, "EUR", confirmed=True)
    with pytest.raises(BankError, match=r"remaining daily transfer limit of 500\.00"):
        bank.transfer("C002", "ACC-2002", "ACC-2001", 600, "EUR")


def test_cards_and_accounts_are_scoped_to_the_customer():
    bank = build_bank()
    assert {a.id for a in bank.accounts_of("C001")} == {"ACC-1001", "ACC-1002"}
    with pytest.raises(BankError, match="not found among your cards"):
        bank.block_card("C003", "CARD-2001", "lost")
    bank.block_card("C003", "CARD-3001", "lost")
    with pytest.raises(BankError, match="already blocked"):
        bank.block_card("C003", "CARD-3001", "lost")


# ----- retrieval -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def index():
    return FaqIndex(load_faq_docs())


@pytest.mark.parametrize(
    ("query", "doc_id"),
    [
        ("replace lost debit card fee", "faq-001"),
        ("daily transfer limit", "faq-002"),
        ("SEPA transfer fee", "faq-003"),
        ("savings interest rate", "faq-006"),
        ("dispute unrecognised card payment", "faq-008"),
        ("bank asked me to move money to a safe account", "faq-009"),
        ("contactless payment limit", "faq-010"),
        ("ATM cash withdrawal abroad", "faq-013"),
    ],
)
def test_bm25_ranks_the_right_document_first(index, query, doc_id):
    assert index.search(query)[0][0].id == doc_id


def test_queries_in_other_languages_find_nothing_in_the_english_faq(index):
    assert index.search("комиссия за перевод") == []
    assert index.search("խնայողական հաշվի տոկոսադրույք") == []


def test_tokenizer_drops_stopwords_and_stems_plurals():
    assert tokenize("The fees for transfers") == ["fee", "transfer"]


def test_poisoned_document_only_exists_in_its_scenario(index):
    assert all(d.id != "faq-099" for d in index.docs)
    extra = apply_scenarios(build_bank(), ["poisoned_faq"])
    poisoned = FaqIndex(load_faq_docs() + extra)
    assert "faq-099" in [d.id for d, _ in poisoned.search("SEPA transfer fee")]


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError, match="Unknown scenario"):
        apply_scenarios(build_bank(), ["nope"])


# ----- datasets --------------------------------------------------------------------------------


def test_datasets_load_and_cover_every_suite_and_language():
    cases = load_cases()
    assert 36 <= len(cases) <= 50
    assert {c.suite for c in cases} == set(SUITES)
    for lang in LANGUAGES:
        assert sum(c.lang == lang for c in cases) >= 8, lang


def test_expected_faq_documents_exist():
    known = {d.id for d in load_faq_docs()}
    for case in load_cases():
        assert set(case.expect.get("faq_docs", [])) <= known, case.id


def test_every_injection_case_has_a_payload_and_a_check():
    for case in load_cases(suites=["injection"]):
        attack_in_data = bool(case.scenarios)
        attack_in_prompt = "ignore all previous instructions" in case.turns[-1]["user"].lower()
        assert attack_in_data or attack_in_prompt, case.id
        assert case.expect.get("judge_notes"), case.id


def test_repeat_subset_is_small_and_mixed():
    subset = load_cases(tags=["k3"])
    assert 4 <= len(subset) <= 10
    assert len({c.suite for c in subset}) == 4

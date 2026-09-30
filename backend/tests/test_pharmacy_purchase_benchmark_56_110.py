"""Regression coverage for the pharmacy purchase-ledger question set (56–110)."""

from pathlib import Path

import pandas as pd
import pytest

import app.schema  # register the pharmacy domain pack
from app.analytics.seam import compute_for_question
from app.rag.router import RouteType, classify_route, extract_filters
from app.schema.domain import get_domain_pack
from app.schema.mapper import map_headers
from app.schema.normalize import normalize


ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "uploads" / "pharmacy_ppp.csv"


@pytest.fixture(scope="module")
def pharmacy_ledger():
    if not CSV.exists():
        pytest.skip("pharmacy_ppp.csv is a local dataset and is not present in this checkout")
    raw = pd.read_csv(CSV, encoding="cp1252")
    mapping = map_headers(list(raw.columns), get_domain_pack("pharmacy"))
    return normalize(raw, mapping, domain="pharmacy", keep_extras=True)


def answer(question, frame):
    assert classify_route(question) == RouteType.ANALYTICS, question
    values, _ = compute_for_question(
        question, frame, extract_filters(question, domain="pharmacy"), domain="pharmacy"
    )
    assert values, question
    return next(iter(values.values()))


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which supplier has supplied the highest total quantity of medicines?", 6410),
        ("Which supplier has the second-highest total quantity supplied?", 6130),
        ("How many units were supplied by Raza Pharma Distributors?", 4530),
        ("Which product group has the highest total purchased quantity?", 7640),
        ("What is the total quantity purchased in the Capsules group?", 6240),
        ("What is the total quantity purchased in the Creams group?", 6170),
        ("Which product group has the lowest total purchased quantity?", 4150),
        ("Which individual record has the highest quantity?", 100),
        ("Which product has the highest purchase price in a single record?", 2613.16),
        ("Which product has the highest sale price in the dataset?", 3507),
        ("How many records contain a bonus quantity?", 489),
        ("How many records have a discount greater than 0%?", 479),
        ("How many records have GST greater than 0%?", 786),
        ("Which location code occurs most frequently?", 21),
        ("In which month was the highest total product amount recorded?", 3116521.11),
        ("Which supplier contributed the highest quantity of purchased products, and what was that quantity?", 6410),
        ("Compare the total purchased quantity of the General and Capsules groups. How much higher is General?", 1400),
        ("Compare Khan Pharma Distributors and Ahmed Pharma Distributors by total quantity supplied.", 280),
        ("Which product was purchased from the greatest number of different supplier names?", 10),
        ("How many different suppliers supplied Arinac 500mg Tab?", 9),
        ("What percentage of all product records have GST greater than zero?", 68.34782608695652),
        ("What percentage of records received a discount?", 41.65217391304348),
        ("What percentage of records include a bonus?", 42.52173913043478),
    ],
)
def test_dataset_benchmark_expected_values(question, expected, pharmacy_ledger):
    result = answer(question, pharmacy_ledger)
    assert result.status == "ok"
    assert result.value == pytest.approx(expected)


def test_max_quantity_answer_reports_ties_instead_of_claiming_a_unique_record(pharmacy_ledger):
    result = answer("Which individual record has the highest quantity?", pharmacy_ledger)
    assert "127 tied" in result.name
    assert result.value == 100


@pytest.mark.parametrize(
    "question",
    [
        "Show me the five products with the highest total purchased quantities.",
        "Which medicines have a margin above 40%?",
        "Which high-quantity medicines also have high margins?",
        "Which suppliers account for the largest purchase quantities?",
        "Which suppliers have supplied the greatest variety of products?",
        "Which products have been purchased from multiple suppliers?",
        "For products available from multiple suppliers, compare their purchase prices.",
        "Which product categories account for the largest purchasing expenditure?",
        "Which products received the largest discounts?",
        "Which products received bonuses most frequently?",
        "Which suppliers provide bonus quantities most frequently?",
        "Which suppliers provide the highest average discount percentage?",
        "Which months had the highest purchasing expenditure?",
        "Compare total purchasing expenditure between 2024 and 2025.",
        "Which medicines are approaching their expiry dates?",
        "Show products that have already expired as of December 31, 2025.",
        "Which suppliers supplied the largest quantity of products that later expired?",
        "Show batches of the same medicine with different expiry dates.",
        "Which product groups contain the most expired batches?",
        "Which supplier received the highest total payment?",
        "Show the top 10 invoices by net payable amount.",
    ],
)
def test_related_owner_questions_are_computed_from_purchase_ledger(question, pharmacy_ledger):
    assert answer(question, pharmacy_ledger).status == "ok"


@pytest.mark.parametrize(
    "question",
    [
        "Which medicine is selling fastest to customers?",
        "Which medicine should I reorder tomorrow?",
        "Which supplier is the most reliable?",
        "Which supplier delivers medicines fastest?",
        "How many tablets are currently in stock?",
        "How much profit did the pharmacy make in 2025?",
        "Which medicine will have the highest demand next month?",
        "Did supplier Khan deliver any fake medicines?",
        "Which supplier should I stop purchasing from?",
        "Why did purchases decrease/increase in a particular month?",
        "Which medicines have the shortest shelf life?",
    ],
)
def test_purchase_only_file_does_not_claim_unavailable_business_facts(question, pharmacy_ledger):
    result = answer(question, pharmacy_ledger)
    assert result.status == "unavailable"
    assert result.reason


def test_non_purchase_questions_keep_rag_and_chitchat_routes():
    assert classify_route("Hi") == RouteType.CHITCHAT
    assert classify_route("What does amoxicillin treat?") == RouteType.RAG

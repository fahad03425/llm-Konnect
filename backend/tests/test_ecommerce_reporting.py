import json
from app.reporting.grounding import parse_response
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from app.reporting.report import gather_report_data, generate_report
from app.reporting.verifier import verify, STATUS_VERIFIED
from app.reporting.narrative import _format_ground_truth_summary
from app.reporting.models import ReportData
from app.analytics.engine import KPIEngine


@pytest.fixture
def ecommerce_report_df():
    return pd.DataFrame([
        {
            "order_id": "#1001",
            "date": "2026-03-01",
            "customer_id": "CUST-01",
            "product_name": "Leather Backpack",
            "product_sku": "BP-LTH-BRN",
            "quantity": 1,
            "unit_price": 120.0,
            "sale_amount": 120.0,
            "cost_per_item": 50.0,
            "discount_amount": 0.0,
            "refund_amount": 0.0,
            "payment_gateway": "Shopify Payments",
            "source_row": 1
        },
        {
            "order_id": "#1002",
            "date": "2026-03-02",
            "customer_id": "CUST-02",
            "product_name": "Canvas Tote Bag",
            "product_sku": "TB-CNV-BLK",
            "quantity": 2,
            "unit_price": 30.0,
            "sale_amount": 60.0,
            "cost_per_item": 15.0,
            "discount_amount": 10.0,
            "refund_amount": 0.0,
            "payment_gateway": "PayPal",
            "source_row": 2
        },
        {
            "order_id": "#1003",
            "date": "2026-03-05",
            "customer_id": "CUST-01",  # Repeat customer
            "product_name": "Leather Cardholder",
            "product_sku": "CH-LTH-BLK",
            "quantity": 1,
            "unit_price": 40.0,
            "sale_amount": 40.0,
            "cost_per_item": 15.0,
            "discount_amount": 0.0,
            "refund_amount": 10.0,
            "payment_gateway": "Shopify Payments",
            "source_row": 3
        }
    ])


def test_ecommerce_report_data_building(ecommerce_report_df):
    report_data = gather_report_data(
        source_df=ecommerce_report_df,
        business_name="Urban Stitch Outfitters",
        domain="ecommerce"
    )

    assert report_data.domain == "ecommerce"
    assert report_data.business_name == "Urban Stitch Outfitters"
    assert "gmv" in report_data.kpis
    assert report_data.kpis["gmv"].value == 220.0  # 120 + 60 + 40
    assert "average_order_value_ecom" in report_data.kpis
    # 220 / 3 = 73.33
    assert report_data.kpis["average_order_value_ecom"].value == 73.33
    assert "repeat_customer_rate_pct" in report_data.kpis
    assert report_data.kpis["repeat_customer_rate_pct"].value == 50.0


def test_ecommerce_ground_truth_formatting(ecommerce_report_df):
    report_data = gather_report_data(
        source_df=ecommerce_report_df,
        business_name="Urban Stitch Outfitters",
        domain="ecommerce"
    )

    summary_text = _format_ground_truth_summary(report_data, "ecommerce")
    assert "Gross Merchandise Value (GMV)" in summary_text
    assert "220" in summary_text
    assert "Average Order Value (AOV)" in summary_text


def test_ecommerce_claim_verification(ecommerce_report_df):
    report_data = gather_report_data(
        source_df=ecommerce_report_df,
        business_name="Urban Stitch Outfitters",
        domain="ecommerce"
    )

    # Narrative with accurate numbers
    truthful_narrative = parse_response(json.dumps({
        "fact_ids": ["gmv", "average_order_value_ecom"], "commentary": [],
    }), report_data)

    ver_report = verify(truthful_narrative, report_data)
    assert ver_report.all_verified is True
    assert len(ver_report.claims) >= 2
    for claim in ver_report.claims:
        assert claim.status == STATUS_VERIFIED


@patch('app.core.llm.llm.generate')
def test_full_ecommerce_report_pipeline(mock_llm_generate, ecommerce_report_df):
    mock_llm_generate.return_value = json.dumps({"fact_ids": ["gmv"], "commentary": []})

    result = generate_report(
        source_df=ecommerce_report_df,
        domain="ecommerce",
        business_name="Urban Stitch Outfitters"
    )

    assert result.verification_passed is True
    assert "gmv" in result.kpi_snapshot
    assert result.narrative is not None
    assert len(result.narrative) > 0

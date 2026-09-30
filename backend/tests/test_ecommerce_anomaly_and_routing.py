import pandas as pd
import pytest

from app.anomaly.detectors import (
    detect_ecommerce_margin_erosion,
    detect_ecommerce_refund_surges,
    detect_ecommerce_review_rating_drops,
    detect_all_anomalies,
)
from app.anomaly.models import AnomalyType, Severity
from app.anomaly.explainer import generate_template_explanation
from app.analytics.seam import AnalyticsRouter
from app.schema.domain import get_domain_pack


def test_ecommerce_margin_erosion_detector():
    df = pd.DataFrame([
        {
            "order_id": "#1001",
            "product_name": "Premium Hoodie",
            "product_sku": "HD-BLK-M",
            "unit_price": 30.0,
            "cost_per_item": 50.0,  # Sold at $20 loss!
            "quantity": 2,
            "sale_amount": 60.0,
            "source_row": 10
        },
        {
            "order_id": "#1002",
            "product_name": "Graphic T-Shirt",
            "product_sku": "TS-WHT-L",
            "unit_price": 25.0,
            "cost_per_item": 10.0,  # Profitable
            "quantity": 1,
            "sale_amount": 25.0,
            "source_row": 11
        }
    ])

    anomalies = detect_ecommerce_margin_erosion(df)
    assert len(anomalies) == 1
    assert anomalies[0].anomaly_type == AnomalyType.MARGIN_EROSION
    assert anomalies[0].observed_value == 30.0
    assert anomalies[0].statistical_score == 20.0  # $20 loss per unit
    assert anomalies[0].source_row == 10

    explanation = generate_template_explanation(anomalies[0])
    assert "sold at $30.00 below its unit cost of $50.00" in explanation


def test_ecommerce_refund_surge_detector():
    df = pd.DataFrame([
        {
            "order_id": "#101",
            "product_sku": "SKU-DEFECT",
            "product_name": "Faulty Power Bank",
            "sale_amount": 100.0,
            "refund_amount": 50.0,
            "source_row": 1
        },
        {
            "order_id": "#102",
            "product_sku": "SKU-DEFECT",
            "product_name": "Faulty Power Bank",
            "sale_amount": 100.0,
            "refund_amount": 40.0,
            "source_row": 2
        },
        {
            "order_id": "#103",
            "product_sku": "SKU-NORMAL",
            "product_name": "Standard Cable",
            "sale_amount": 200.0,
            "refund_amount": 0.0,
            "source_row": 3
        }
    ])

    anomalies = detect_ecommerce_refund_surges(df)
    assert len(anomalies) == 1
    assert anomalies[0].anomaly_type == AnomalyType.REFUND_SURGE
    # Total sales = 200, Total refund = 90 -> 45% refund rate
    assert anomalies[0].observed_value == 45.0
    assert anomalies[0].severity == Severity.HIGH


def test_ecommerce_review_rating_drop_detector():
    df = pd.DataFrame([
        {
            "product_sku": "SKU-BAD-CHAIR",
            "product_name": "Broken Office Chair",
            "rating": 1.0,
            "review_text": "Arrived broken, bad quality.",
            "source_row": 1
        },
        {
            "product_sku": "SKU-BAD-CHAIR",
            "product_name": "Broken Office Chair",
            "rating": 2.0,
            "review_text": "Missing screws, uncomfortable.",
            "source_row": 2
        },
        {
            "product_sku": "SKU-GOOD-DESK",
            "product_name": "Solid Wood Desk",
            "rating": 5.0,
            "review_text": "Amazing desk!",
            "source_row": 3
        }
    ])

    anomalies = detect_ecommerce_review_rating_drops(df)
    assert len(anomalies) == 1
    assert anomalies[0].anomaly_type == AnomalyType.REVIEW_RATING_DROP
    assert anomalies[0].observed_value == 1.5  # (1+2)/2 = 1.5
    assert anomalies[0].severity == Severity.HIGH


def test_detect_all_anomalies_for_ecommerce_domain():
    df = pd.DataFrame([
        {
            "order_id": "#1001",
            "product_name": "Loss Leader Gadget",
            "product_sku": "SKU-LOSS",
            "unit_price": 10.0,
            "cost_per_item": 25.0,
            "quantity": 1,
            "sale_amount": 10.0,
            "refund_amount": 0.0,
            "source_row": 1
        }
    ])

    result = detect_all_anomalies(df, domain="ecommerce")
    assert result.total_anomalies >= 1
    types = [a.anomaly_type for a in result.anomalies]
    assert AnomalyType.MARGIN_EROSION in types


def test_ecommerce_chatbot_seam_routing():
    # Verify that EcommerceDomainPack question rules route queries to the right domain KPIs
    pack = get_domain_pack("ecommerce")
    rules = pack.kpi_question_rules
    assert len(rules) > 0

    # Test that AnalyticsRouter matches domain questions
    router = AnalyticsRouter()
    
    sample_ecom_df = pd.DataFrame([
        {
            "order_id": "ORD-1",
            "date": "2026-03-01",
            "customer_id": "C-1",
            "product_sku": "SKU-1",
            "product_name": "Product 1",
            "quantity": 2,
            "unit_price": 50.0,
            "sale_amount": 100.0,
            "discount_amount": 10.0,
            "refund_amount": 0.0,
            "source_row": 1
        },
        {
            "order_id": "ORD-2",
            "date": "2026-03-02",
            "customer_id": "C-1",  # Repeat
            "product_sku": "SKU-2",
            "product_name": "Product 2",
            "quantity": 1,
            "unit_price": 50.0,
            "sale_amount": 50.0,
            "discount_amount": 0.0,
            "refund_amount": 0.0,
            "source_row": 2
        }
    ])
    
    kb_records = sample_ecom_df.to_dict(orient="records")
    
    # 1. AOV question
    computed, rows = router.compute("what is our aov?", {}, kb_records, domain="ecommerce")
    assert "average_order_value_ecom" in computed
    # (100 + 50) / 2 = 75.0
    assert computed["average_order_value_ecom"]["value"] == 75.0

    # 2. Repeat customer question
    computed, rows = router.compute("show customer repeat rate", {}, kb_records, domain="ecommerce")
    assert "repeat_customer_rate_pct" in computed
    assert computed["repeat_customer_rate_pct"]["value"] == 100.0

    # 3. GMV question
    computed, rows = router.compute("what is total gross merchandise value gmv?", {}, kb_records, domain="ecommerce")
    assert "gmv" in computed
    assert computed["gmv"]["value"] == 150.0

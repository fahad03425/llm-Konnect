import pandas as pd
import pytest

from app.analytics.engine import KPIEngine
from app.analytics.filters import KPIFilters
from app.analytics.models import STATUS_OK, STATUS_UNAVAILABLE
from app.analytics.domains.ecommerce import (
    gmv,
    ecommerce_net_sales,
    average_order_value_ecom,
    total_discounts_applied,
    total_refunds_ecom,
    refund_rate_pct_ecom,
    ecom_gross_profit,
    ecom_gross_margin_pct,
    unique_customers_count,
    repeat_customer_rate_pct,
    avg_items_per_order,
    slow_moving_skus_count,
    avg_customer_rating,
)


@pytest.fixture
def sample_ecommerce_df():
    return pd.DataFrame([
        {
            "order_id": "ORD-101",
            "date": "2026-03-01",
            "customer_id": "CUST-001",
            "product_sku": "SKU-A",
            "product_name": "Wireless Mouse",
            "quantity": 2,
            "unit_price": 25.0,
            "sale_amount": 50.0,
            "cost_per_item": 15.0,
            "discount_amount": 5.0,
            "refund_amount": 0.0,
            "rating": 5.0,
            "source_row": 1
        },
        {
            "order_id": "ORD-101",
            "date": "2026-03-01",
            "customer_id": "CUST-001",
            "product_sku": "SKU-B",
            "product_name": "Mousepad",
            "quantity": 1,
            "unit_price": 10.0,
            "sale_amount": 10.0,
            "cost_per_item": 4.0,
            "discount_amount": 0.0,
            "refund_amount": 0.0,
            "rating": 4.0,
            "source_row": 2
        },
        {
            "order_id": "ORD-102",
            "date": "2026-03-05",
            "customer_id": "CUST-001",  # Repeat customer!
            "product_sku": "SKU-A",
            "product_name": "Wireless Mouse",
            "quantity": 1,
            "unit_price": 25.0,
            "sale_amount": 25.0,
            "cost_per_item": 15.0,
            "discount_amount": 0.0,
            "refund_amount": 0.0,
            "rating": 5.0,
            "source_row": 3
        },
        {
            "order_id": "ORD-103",
            "date": "2026-03-10",
            "customer_id": "CUST-002",
            "product_sku": "SKU-C",
            "product_name": "Mechanical Keyboard",
            "quantity": 1,
            "unit_price": 100.0,
            "sale_amount": 100.0,
            "cost_per_item": 50.0,
            "discount_amount": 10.0,
            "refund_amount": 20.0,
            "rating": 3.0,
            "source_row": 4
        }
    ])


def test_gmv_and_net_sales(sample_ecommerce_df):
    filters = KPIFilters()
    res_gmv = gmv(sample_ecommerce_df, filters)
    assert res_gmv.status == STATUS_OK
    # 50 + 10 + 25 + 100 = 185.0
    assert res_gmv.value == 185.0

    res_net = ecommerce_net_sales(sample_ecommerce_df, filters)
    assert res_net.status == STATUS_OK
    # 185 - 20 (refunds) = 165.0
    assert res_net.value == 165.0


def test_average_order_value(sample_ecommerce_df):
    filters = KPIFilters()
    res_aov = average_order_value_ecom(sample_ecommerce_df, filters)
    assert res_aov.status == STATUS_OK
    # Total revenue = 185.0, Unique orders = 3 (ORD-101, ORD-102, ORD-103)
    # AOV = 185.0 / 3 = 61.67
    assert res_aov.value == 61.67


def test_discounts_and_refunds(sample_ecommerce_df):
    filters = KPIFilters()
    res_disc = total_discounts_applied(sample_ecommerce_df, filters)
    assert res_disc.status == STATUS_OK
    # 5 + 0 + 0 + 10 = 15.0
    assert res_disc.value == 15.0

    res_ref = total_refunds_ecom(sample_ecommerce_df, filters)
    assert res_ref.status == STATUS_OK
    assert res_ref.value == 20.0

    res_ref_rate = refund_rate_pct_ecom(sample_ecommerce_df, filters)
    assert res_ref_rate.status == STATUS_OK
    # (20 / 185) * 100 = 10.81%
    assert res_ref_rate.value == 10.81


def test_gross_profit_and_margin(sample_ecommerce_df):
    filters = KPIFilters()
    res_profit = ecom_gross_profit(sample_ecommerce_df, filters)
    assert res_profit.status == STATUS_OK
    # Revenue = 185
    # COGS = (2*15) + (1*4) + (1*15) + (1*50) = 30 + 4 + 15 + 50 = 99
    # Profit = 185 - 99 = 86.0
    assert res_profit.value == 86.0

    res_margin = ecom_gross_margin_pct(sample_ecommerce_df, filters)
    assert res_margin.status == STATUS_OK
    # 86 / 185 * 100 = 46.49%
    assert res_margin.value == 46.49


def test_customer_retention_and_orders(sample_ecommerce_df):
    filters = KPIFilters()
    res_cust = unique_customers_count(sample_ecommerce_df, filters)
    assert res_cust.status == STATUS_OK
    assert res_cust.value == 2  # CUST-001, CUST-002

    res_rep = repeat_customer_rate_pct(sample_ecommerce_df, filters)
    assert res_rep.status == STATUS_OK
    # CUST-001 has 2 orders (ORD-101, ORD-102), CUST-002 has 1. Repeat rate = 1/2 = 50%
    assert res_rep.value == 50.0

    res_items = avg_items_per_order(sample_ecommerce_df, filters)
    assert res_items.status == STATUS_OK
    # Total units = 2 + 1 + 1 + 1 = 5 units across 3 orders -> 5/3 = 1.67
    assert res_items.value == 1.67


def test_reviews_and_slow_skus(sample_ecommerce_df):
    filters = KPIFilters()
    res_rating = avg_customer_rating(sample_ecommerce_df, filters)
    assert res_rating.status == STATUS_OK
    # Ratings: 5, 4, 5, 3 -> (17 / 4) = 4.25
    assert res_rating.value == 4.25

    res_slow = slow_moving_skus_count(sample_ecommerce_df, filters)
    assert res_slow.status == STATUS_OK
    # SKU-A sold 3 units, SKU-B sold 1 unit, SKU-C sold 1 unit -> 2 SKUs with <= 1 unit sold
    assert res_slow.value == 2


def test_kpi_engine_ecommerce_integration(sample_ecommerce_df):
    engine = KPIEngine()
    results = engine.compute_all(sample_ecommerce_df, domain="ecommerce")
    
    # Check that core + ecommerce domain KPIs are present in dictionary
    keys = list(results.keys())
    assert "gmv" in keys
    assert "ecommerce_net_sales" in keys
    assert "average_order_value_ecom" in keys
    assert "repeat_customer_rate_pct" in keys
    assert "avg_customer_rating" in keys
    assert "ecom_gross_profit" in keys
    assert results["gmv"].status == STATUS_OK
    assert results["gmv"].value == 185.0

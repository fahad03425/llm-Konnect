import pandas as pd
from app.analytics.filters import KPIFilters, apply_filters
from app.rag.router import extract_filters
from app.rag.chat import RAGChat

def test_single_day_date_aware_analytics():
    df = pd.DataFrame([
        {'date': '2024-09-14', 'net_payable': 500.0, 'quantity': 5},
        {'date': '2024-09-15', 'net_payable': 1200.0, 'quantity': 10},
        {'date': '2024-09-15', 'net_payable': 800.0, 'quantity': 8},
        {'date': '2024-09-16', 'net_payable': 300.0, 'quantity': 2},
        {'date': '2024-09-20', 'net_payable': 1500.0, 'quantity': 12},
    ])

    # 1. English standard "what were my sale on 15 september"
    q1 = "what were my sale on 15 september"
    f1 = extract_filters(q1)
    res_f1 = RAGChat._resolve_relative_date_filter(f1, df)
    kpi_f1 = KPIFilters(**{k: v for k, v in res_f1.items() if hasattr(KPIFilters, k)})
    filtered1, _ = apply_filters(df, kpi_f1)
    assert len(filtered1) == 2
    assert filtered1['net_payable'].sum() == 2000.0

    # 2. Typo in month "revenue on 15th septembr 2024"
    q2 = "revenue on 15th septembr 2024"
    f2 = extract_filters(q2)
    res_f2 = RAGChat._resolve_relative_date_filter(f2, df)
    kpi_f2 = KPIFilters(**{k: v for k, v in res_f2.items() if hasattr(KPIFilters, k)})
    filtered2, _ = apply_filters(df, kpi_f2)
    assert len(filtered2) == 2
    assert filtered2['net_payable'].sum() == 2000.0

    # 3. Numeric date "sales on 15-09-2024"
    q3 = "sales on 15-09-2024"
    f3 = extract_filters(q3)
    res_f3 = RAGChat._resolve_relative_date_filter(f3, df)
    kpi_f3 = KPIFilters(**{k: v for k, v in res_f3.items() if hasattr(KPIFilters, k)})
    filtered3, _ = apply_filters(df, kpi_f3)
    assert len(filtered3) == 2
    assert filtered3['net_payable'].sum() == 2000.0

    # 4. Month day order "sales on septembr 15"
    q4 = "sales on septembr 15"
    f4 = extract_filters(q4)
    res_f4 = RAGChat._resolve_relative_date_filter(f4, df)
    kpi_f4 = KPIFilters(**{k: v for k, v in res_f4.items() if hasattr(KPIFilters, k)})
    filtered4, _ = apply_filters(df, kpi_f4)
    assert len(filtered4) == 2
    assert filtered4['net_payable'].sum() == 2000.0

    # 5. Date range "from 14 sep to 16 sep"
    q5 = "from 14 septembr to 16 septembr"
    f5 = extract_filters(q5)
    res_f5 = RAGChat._resolve_relative_date_filter(f5, df)
    kpi_f5 = KPIFilters(**{k: v for k, v in res_f5.items() if hasattr(KPIFilters, k)})
    filtered5, _ = apply_filters(df, kpi_f5)
    assert len(filtered5) == 4
    assert filtered5['net_payable'].sum() == 2800.0


def test_date_ranges_accept_common_written_and_numeric_formats():
    expected = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    for phrase in (
        "from 2026-09-01 to 2026-09-30",
        "from 2026.09.01 to 2026.09.30",
        "from 01/09/2026 to 30/09/2026",
        "between September 1 2026 and September 30 2026",
        "between 1 Sep 2026 and 30 Sep 2026",
    ):
        assert extract_filters(f"Show sales {phrase}", "pharmacy") == expected


def test_expiry_cutoff_is_not_extracted_as_a_sales_date_filter():
    assert extract_filters("How many inventory batches expire before 1 January 2027?", "pharmacy") == {}

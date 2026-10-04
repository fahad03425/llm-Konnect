import csv

import pandas as pd
import pytest

from app.connectors.csv_excel import CSVConnector
from app.reporting.grounding import render
from app.reporting.report import _render_source_driven_html_document, gather_report_data
from app.reporting.verifier import VerificationReport


def test_csv_connector_recovers_unquoted_comma_in_trailing_review(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(
        "order_id,amount,review_text\n"
        '1,10,"Works well"\n'
        "2,20,Second purchase, love the packaging!\n",
        encoding="utf-8",
    )

    connector = CSVConnector(str(path))
    frame = connector.fetch()

    assert frame["review_text"].tolist() == ["Works well", "Second purchase, love the packaging!"]
    assert connector.preview(n=1)["review_text"].tolist() == ["Works well"]


def test_csv_connector_still_rejects_bad_non_text_rows(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("order_id,amount\n1,10\n2,20,unexpected\n", encoding="utf-8")

    with pytest.raises(pd.errors.ParserError):
        CSVConnector(str(path)).fetch()


@pytest.mark.parametrize("commentary", ["Revenue grew 99%.", "Revenue grew five percent."])
def test_grounded_commentary_cannot_bypass_fact_catalog_with_numbers(commentary):
    with pytest.raises(ValueError, match="must not contain numeric claims"):
        render(["total_revenue"], [commentary], {"total_revenue": (100.0, "USD", "Total revenue")})


def test_incomplete_embedded_source_is_carried_into_html_and_pdf(tmp_path, monkeypatch):
    frame = pd.DataFrame({"revenue": [100.0]})
    frame.attrs["incomplete_source"] = True

    report_data = gather_report_data(
        source_df=frame,
        domain="retail",
        business_name="Test business",
        kpi_results={},
    )
    html = _render_source_driven_html_document(report_data, "", VerificationReport(), {})

    assert report_data.source_data_complete is False
    assert report_data.source_data_warning
    assert "Incomplete source data" in html
    assert "Source data completeness: Incomplete; results are partial" in html

    import app.reporting.pdf_report as pdf_report
    paragraph_text = []
    actual_paragraph = pdf_report.Paragraph

    def capture_paragraph(text, *args, **kwargs):
        paragraph_text.append(str(text))
        return actual_paragraph(text, *args, **kwargs)

    monkeypatch.setattr(pdf_report, "Paragraph", capture_paragraph)

    pdf_path = pdf_report.build_pdf_report(report_data, "", VerificationReport(), {}, tmp_path / "partial-report.pdf")
    assert pdf_path.is_file()
    assert any("Incomplete source data" in text for text in paragraph_text)

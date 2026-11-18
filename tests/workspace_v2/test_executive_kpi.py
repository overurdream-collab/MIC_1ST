"""Executive KPI dashboard reads frozen synthetic results without mutating them."""
from decimal import Decimal

import pytest

from tests.workspace_v2.net_cycle_fixtures import (
    approve_batch_policies,
    case_row,
    configure_signers,
    execute_batch,
    import_cases,
    result_files,
)
from tests.api.conftest import api_factory, synthetic_password_hash
from tests.features.conftest import features_api
from tests.workspace_v2.test_access_and_registry import grants, workspace
from tests.workspace_v2.test_linked_references import linked


@pytest.fixture
def kpi_cycle(linked, monkeypatch):
    configure_signers(linked, monkeypatch)
    return linked


def _cycle(api):
    cases = [
        case_row(1),
        case_row(2, gross="1200", unit="1200", copay="120", net="1080"),
        case_row(3, name="SYN wrong identity"),
        case_row(4),
    ]
    batch = import_cases(api, cases, missing_references={cases[3]["invoice_reference"]})
    approve_batch_policies(api, batch.batch_id)
    run = execute_batch(api, batch.batch_id)
    result, _ = result_files(api, batch.batch_id)
    return batch, run, result


def test_executive_kpi_uses_frozen_results_and_separates_currency(kpi_cycle):
    api = kpi_cycle
    batch, run, result = _cycle(api)
    response = api.client.get("/api/v1/analytics/kpi", headers=api.headers["root"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_lines"] == 4
    assert body["summary"]["counts"] == run["counts"] == {
        "accepted": 1, "rejected": 1, "held": 1, "review": 1,
    }
    assert body["summary"]["unverified"] == 2
    assert body["summary"]["rates"]["matched"] == 0.25
    assert body["summary"]["single_currency"] is True
    assert body["summary"]["currency_count"] == 1
    money = body["summary"]["by_currency"]["LYD"]
    assert Decimal(money["claimed"]) == Decimal("3780")
    assert Decimal(money["flagged"]) == Decimal("2880")
    assert Decimal(money["proposed_difference"]) == Decimal("180")
    assert Decimal(money["price_variance"]) == Decimal("200")
    assert body["quality"]["reconciliation"]["status"] == "reconciled"
    assert body["quality"]["reconciliation"]["result_lines"] == 4
    assert body["quality"]["rules_run"]["count"] == 8
    assert body["quality"]["audit_duration"]["available"] is False
    assert body["options"]["currencies"] == ["LYD"]
    assert body["options"]["batches"][0]["value"] == batch.batch_id
    assert body["scope"]["result_ids"] == [result["id"]]
    assert body["scope"]["truncated"] is False


def test_kpi_filters_and_line_drilldown_are_consistent(kpi_cycle):
    api = kpi_cycle
    _cycle(api)
    base = "/api/v1/analytics/kpi"
    unverified = api.client.get(base + "?outcome=unverified", headers=api.headers["root"])
    assert unverified.status_code == 200, unverified.text
    assert unverified.json()["summary"]["total_lines"] == 2

    price = api.client.get(base + "?rule=price", headers=api.headers["root"]).json()
    assert price["summary"]["total_lines"] == 1
    assert price["summary"]["counts"]["rejected"] == 1

    future = api.client.get(base + "?date_from=2099-01-01", headers=api.headers["root"]).json()
    assert future["summary"]["total_lines"] == 0
    assert future["summary"]["rates"]["matched"] is None

    invalid = api.client.get(base + "?date_from=2026-02-01&date_to=2026-01-01", headers=api.headers["root"])
    assert invalid.status_code == 400

    lines = api.client.get("/api/v1/analytics/lines?outcome=flagged&limit=10", headers=api.headers["root"])
    assert lines.status_code == 200, lines.text
    page = lines.json()
    assert page["total"] == 3
    assert all(row["bucket"] != "accepted" for row in page["items"])
    assert all("source_values" not in row for row in page["items"])
    assert any(row["price_variance"] is not None and Decimal(row["price_variance"]) == Decimal("200") for row in page["items"])


def test_kpi_requires_reports_permission_and_tenant_scope(kpi_cycle):
    api = kpi_cycle
    _cycle(api)
    assert api.client.get("/api/v1/analytics/kpi", headers=api.headers["other"]).status_code == 403
    assert grants(api, ["reports.read"], user="admin").status_code == 200
    allowed = api.client.get("/api/v1/analytics/kpi", headers=api.headers["admin"])
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["summary"]["total_lines"] == 4

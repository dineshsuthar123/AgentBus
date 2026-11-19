from agentbus.repair.live.contracts import PurchasePage
from agentbus.repair.live.transport import ApplicationConnector, ConnectorError


def check_conformance(manifest, purchase_ids=()):
    """Safe against a real configured deployment: reads only, no seeded accounts."""
    client = ApplicationConnector(manifest)
    capabilities = client.capabilities()
    checks = {"authenticated_identity": capabilities.identity == manifest.identity,
              "semantic_receipts_advertised": capabilities.semantic_receipts,
              "aggregate_writers_advertised": capabilities.aggregate_serialization,
              "normal_access_path_advertised": capabilities.normal_access_verification,
              "indefinite_receipts": capabilities.receipt_retention == "indefinite"}
    if purchase_ids:
        if len(purchase_ids) > 100:
            raise ConnectorError("connector_invalid", status=400)
        page = PurchasePage.model_validate(client.request("POST", "/purchases", {"purchase_ids": list(purchase_ids)}))
        checks["complete_bounded_read"] = page.complete and page.next_cursor is None and page.identity == manifest.identity
        checks["exact_mapping"] = len(page.records) == len(purchase_ids) and {row.purchase_id for row in page.records} == set(purchase_ids)
    return {"ok": all(checks.values()), "checks": checks, "writes": 0,
            "scope": "Deployed read-contract check. Writer consistency and fault conformance require the disposable PostgreSQL adversarial suite plus application-owner hook review.",
            "production_transport": manifest.transport == "https"}

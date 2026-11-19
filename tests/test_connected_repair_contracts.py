from datetime import timedelta
import json

import pytest
from pydantic import ValidationError

from agentbus.repair.integration.reference import fixture
from agentbus.repair.live.contracts import ConnectionManifest, Identity, RepairPolicy, StripeConfiguration, now
from agentbus.repair.live.decisions import decide
from agentbus.repair.live.transport import ConnectorError, validate_destination


def identity():
    return Identity(application="app", tenant="tenant", environment="sandbox", configuration_revision="one")


@pytest.mark.parametrize("updates,expected", [
    ({}, "eligible"), ({"payment_state": "authorized"}, "investigation_required"),
    ({"payment_state": "pending"}, "investigation_required"), ({"payment_state": "failed"}, "subsequent_history"),
    ({"refunded_minor": 1}, "investigation_required"), ({"refunded_minor": 2000}, "subsequent_history"),
    ({"disputed": True}, "subsequent_history"), ({"pending_payment_change": True}, "conflicted_stale"),
    ({"cancellation": "period_end"}, "eligible"), ({"cancellation": "immediate"}, "subsequent_history"),
    ({"restriction": "suspended"}, "subsequent_history"), ({"restriction": "deleted"}, "subsequent_history"),
    ({"restriction": "manual_override"}, "subsequent_history"), ({"restriction": "changed_product"}, "subsequent_history"),
    ({"history_complete": False}, "investigation_required"), ({"mapping_verified": False}, "investigation_required"),
    ({"access_present": None}, "investigation_required"), ({"access_present": True}, "already_correct"),
    ({"stripe_customer": "cus_external"}, "investigation_required"),
])
def test_deterministic_policy_business_edges(updates, expected):
    at = now()
    state = next(fixture(identity())).model_copy(update=updates)
    policy = RepairPolicy(incident_start=at - timedelta(days=2), incident_end=at)
    assert decide(state, policy, identity(), at).disposition == expected


def test_period_timezone_interval_freshness_and_identity():
    at = now()
    state = next(fixture(identity()))
    policy = RepairPolicy(incident_start=at - timedelta(days=2), incident_end=at)
    assert decide(state.model_copy(update={"period_end": at}), policy, identity(), at).disposition == "investigation_required"
    assert decide(state.model_copy(update={"observed_at": at - timedelta(minutes=6)}), policy, identity(), at).disposition == "conflicted_stale"
    assert decide(state, policy, identity().model_copy(update={"tenant": "other"}), at).disposition == "conflicted_stale"
    assert decide(state.model_copy(update={"purchased_at": policy.incident_end}), policy, identity(), at).disposition == "investigation_required"
    assert decide(state.model_copy(update={"purchased_at": policy.incident_start}), policy, identity(), at).disposition == "eligible"
    with pytest.raises(ValidationError):
        RepairPolicy(incident_start=at.replace(tzinfo=None), incident_end=at)


def manifest(**changes):
    fields = dict(connection_id="safe", identity=identity(), destination="http://127.0.0.1:8767/syndra-correction/v1",
                  approved_addresses=["127.0.0.1"], credential_ref="env:SYNDRA_REPAIR_TEST", revision=1,
                  transport="disposable_loopback", permissions=["read"])
    return ConnectionManifest(**{**fields, **changes})


@pytest.mark.parametrize("destination,addresses", [
    ("http://169.254.169.254/syndra-correction/v1", ["169.254.169.254"]),
    ("https://127.0.0.1/syndra-correction/v1", ["127.0.0.1"]),
    ("http://user:password@127.0.0.1/syndra-correction/v1", ["127.0.0.1"]),
    ("http://[::ffff:127.0.0.1]/syndra-correction/v1", ["::ffff:127.0.0.1"]),
    ("http://127.0.0.1/anything", ["127.0.0.1"]),
    ("http://127.0.0.1/syndra-correction/v1?secret=value", ["127.0.0.1"]),
])
def test_destination_rejects_metadata_credentials_paths_and_mapped_ipv6(destination, addresses):
    with pytest.raises(ConnectorError):
        validate_destination(manifest(destination=destination, approved_addresses=addresses))


def test_pinned_address_prevents_dns_rebinding_and_proxy_inheritance(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://169.254.169.254:80")
    validate_destination(manifest())
    monkeypatch.setattr("agentbus.repair.live.transport.socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 80))])
    with pytest.raises(ConnectorError):
        validate_destination(manifest())


def test_production_never_accepts_disposable_http():
    with pytest.raises(ConnectorError):
        validate_destination(manifest(identity=identity().model_copy(update={"environment": "production"})))


def test_official_stripe_sdk_reads_pinned_contract_and_complete_pages(monkeypatch):
    stripe = pytest.importorskip("stripe")
    from stripe._http_client import HTTPClient
    from agentbus.repair.live.stripe_evidence import StripeEvidence
    monkeypatch.setenv("SYNDRA_REPAIR_STRIPE_FIXTURE", "rk_test_" + "synthetic" * 5)
    calls = []
    class FixtureHTTP(HTTPClient):
        name = "version-pinned-offline-fixture"
        def request(self, method, url, headers, post_data=None, **kwargs):
            from urllib.parse import urlsplit, parse_qs
            path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
            calls.append((method, path, dict(headers), query))
            if path == "/v1/account":
                value = {"id": "acct_fixture", "object": "account"}
            elif path == "/v1/payment_intents/pi_fixture":
                value = {"object": "payment_intent", "id": "pi_fixture", "livemode": False, "status": "succeeded", "amount_received": 2000, "currency": "usd", "customer": "cus_fixture"}
            elif path == "/v1/charges":
                value = {"object": "list", "has_more": False, "data": [{"id": "ch_fixture", "customer": "cus_fixture", "object": "charge", "payment_intent": "pi_fixture", "livemode": False, "paid": True, "captured": True, "status": "succeeded", "currency": "usd", "amount_captured": 2000, "amount_refunded": 0, "disputed": False}]}
            elif path in {"/v1/refunds", "/v1/disputes"}:
                value = {"object": "list", "data": [], "has_more": False}
            else:
                raise AssertionError(path)
            return json.dumps(value), 200, {"content-type": "application/json"}
    state = next(fixture(identity())).model_copy(update={"payment_id": "pi_fixture", "stripe_customer": "cus_fixture"})
    config = StripeConfiguration(credential_ref="env:SYNDRA_REPAIR_STRIPE_FIXTURE", platform_account="acct_fixture", livemode=False)
    evidence = StripeEvidence(config, http_client=FixtureHTTP())
    assert "agree" in evidence.validate_purchase(state)
    assert len(calls) == 5
    assert all(method.lower() == "get" and headers.get("Stripe-Version") == "2026-08-26.dahlia" for method, _path, headers, _query in calls)
    assert stripe.VERSION == "15.6.1"


def test_stripe_provider_debug_logging_is_rejected_before_any_request(monkeypatch):
    stripe = pytest.importorskip("stripe")
    from agentbus.repair.live.stripe_evidence import StripeEvidence
    monkeypatch.setattr(stripe, "log", "debug")
    with pytest.raises(ConnectorError):
        StripeEvidence(StripeConfiguration(credential_ref="env:SYNDRA_REPAIR_FIXTURE", platform_account="acct_fixture", livemode=False))


def test_stripe_pagination_is_complete_and_duplicate_or_incomplete_pages_block(monkeypatch):
    pytest.importorskip("stripe")
    from agentbus.repair.live.stripe_evidence import StripeEvidence
    monkeypatch.setenv("SYNDRA_REPAIR_STRIPE_FIXTURE", "rk_test_" + "synthetic" * 5)
    evidence = StripeEvidence(StripeConfiguration(credential_ref="env:SYNDRA_REPAIR_STRIPE_FIXTURE", platform_account="acct_fixture", livemode=False))
    pages = iter([{"data": [{"id": "one", "livemode": False}], "has_more": True}, {"data": [{"id": "two", "livemode": False}], "has_more": False}])
    parameters = []
    class Page:
        def __init__(self, value): self.value = value
        def to_dict(self): return self.value
    def read(*, params, options):
        parameters.append(params)
        return Page(next(pages))
    assert [row["id"] for row in evidence._pages(read, {})] == ["one", "two"]
    assert parameters[1]["starting_after"] == "one"
    with pytest.raises(ConnectorError):
        list(evidence._pages(lambda **kwargs: Page({"data": [], "has_more": True}), {}))
    with pytest.raises(ConnectorError):
        list(evidence._pages(lambda **kwargs: Page({"data": [{"id": "repeat", "livemode": False}], "has_more": True}), {}))


def test_application_openapi_is_fresh_and_contains_typed_receipts():
    from pathlib import Path
    from agentbus.repair.integration.contract import contract
    value = contract()
    assert Path("agentbus/repair/integration/correction-v1.openapi.json").read_text() == value
    schema = json.loads(value)
    operation = schema["paths"]["/syndra-correction/v1/operations"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("Receipt")


def test_stripe_connected_invoice_subscription_and_all_entitlement_pages(monkeypatch):
    pytest.importorskip("stripe")
    from stripe._http_client import HTTPClient
    from agentbus.repair.live.stripe_evidence import StripeEvidence
    monkeypatch.setenv("SYNDRA_REPAIR_STRIPE_BILLING", "rk_test_" + "synthetic" * 5)
    state = next(fixture(identity())).model_copy(update={"payment_id": "pi_fixture", "stripe_customer": "cus_fixture",
        "stripe_invoice": "in_fixture", "stripe_subscription": "sub_fixture", "stripe_product": "prod_fixture",
        "stripe_feature": "feat_required", "cancellation": "period_end"})
    state = state.model_copy(update={"period_start": state.period_start.replace(microsecond=0), "period_end": state.period_end.replace(microsecond=0)})
    calls = []
    def listed(data, more=False): return {"object": "list", "data": data, "has_more": more}
    class BillingHTTP(HTTPClient):
        name = "official-sdk-offline-billing-contract"
        def request(self, method, url, headers, post_data=None, **kwargs):
            from urllib.parse import urlsplit, parse_qs
            path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
            calls.append((path, dict(headers), query))
            mode = {"livemode": False}
            if path == "/v1/account": value = {"id": headers.get("Stripe-Account", "acct_platform"), "object": "account"}
            elif path == "/v1/payment_intents/pi_fixture": value = {"id": "pi_fixture", "object": "payment_intent", **mode, "customer": "cus_fixture", "status": "succeeded", "amount_received": 2000, "currency": "usd"}
            elif path == "/v1/charges": value = listed([{**mode, "id": "ch_fixture", "object": "charge", "customer": "cus_fixture", "payment_intent": "pi_fixture", "paid": True, "captured": True, "status": "succeeded", "amount_captured": 2000, "currency": "usd", "amount_refunded": 0, "disputed": False}])
            elif path in {"/v1/refunds", "/v1/disputes"}: value = listed([])
            elif path == "/v1/invoices/in_fixture": value = {**mode, "id": "in_fixture", "object": "invoice", "customer": "cus_fixture", "status": "paid", "currency": "usd", "amount_paid": 2000}
            elif path == "/v1/subscriptions/sub_fixture": value = {**mode, "id": "sub_fixture", "object": "subscription", "customer": "cus_fixture", "status": "active", "cancel_at_period_end": True, "pause_collection": None, "pending_update": None}
            elif path == "/v1/invoice_payments": value = listed([{**mode, "id": "inpay_fixture", "object": "invoice_payment", "status": "paid", "payment": {"type": "payment_intent", "payment_intent": "pi_fixture"}}])
            elif path == "/v1/invoices/in_fixture/lines": value = listed([{**mode, "id": "il_fixture", "object": "line_item", "currency": "usd", "amount": 2000, "period": {"start": int(state.period_start.timestamp()), "end": int(state.period_end.timestamp())}, "pricing": {"price_details": {"product": "prod_fixture"}}, "parent": {"subscription_item_details": {"subscription": "sub_fixture"}}}])
            elif path == "/v1/subscription_items": value = listed([{"id": "si_fixture", "object": "subscription_item", "price": {**mode, "product": "prod_fixture"}, "current_period_start": int(state.period_start.timestamp()), "current_period_end": int(state.period_end.timestamp())}])
            elif path == "/v1/entitlements/active_entitlements":
                value = listed([{**mode, "id": "ent_second", "object": "entitlements.active_entitlement", "feature": "feat_required"}]) if query.get("starting_after") else listed([{**mode, "id": "ent_first", "object": "entitlements.active_entitlement", "feature": "feat_other"}], True)
            else: raise AssertionError(path)
            return json.dumps(value), 200, {"content-type": "application/json"}
    config = StripeConfiguration(credential_ref="env:SYNDRA_REPAIR_STRIPE_BILLING", platform_account="acct_platform", connected_account="acct_connected", livemode=False, use_entitlements=True)
    assert "agree" in StripeEvidence(config, http_client=BillingHTTP()).validate_purchase(state)
    assert len([call for call in calls if call[0] == "/v1/entitlements/active_entitlements"]) == 2
    assert all(headers.get("Stripe-Account") == "acct_connected" for _path, headers, _query in calls[1:])
    with pytest.raises(ConnectorError):
        StripeEvidence(config, http_client=BillingHTTP()).validate_purchase(state.model_copy(update={"period_start": state.period_start.replace(microsecond=1)}))


def test_stripe_retry_after_survives_new_clients_and_does_not_patch_shared_time(monkeypatch):
    pytest.importorskip("stripe")
    from agentbus.repair.live import stripe_evidence
    clock = [0.0]
    monkeypatch.setattr(stripe_evidence, "_clock", lambda: clock[0])
    monkeypatch.setenv("SYNDRA_REPAIR_COOLDOWN", "rk_test_" + "synthetic" * 5)
    config = StripeConfiguration(credential_ref="env:SYNDRA_REPAIR_COOLDOWN", platform_account="acct_cooldown_fixture", livemode=False)
    first = stripe_evidence.StripeEvidence(config)
    def limited(): raise ConnectorError("connector_rate_limited", status=429, retry_after=120)
    with pytest.raises(ConnectorError): first._call(limited)
    calls = []
    class Success:
        def to_dict(self): return {"id": "accepted"}
    def read():
        calls.append(True)
        return Success()
    # Rotating a key or constructing a new per-purchase client cannot bypass the
    # account's observed Retry-After. No shared time.sleep monkeypatch is used.
    monkeypatch.setenv("SYNDRA_REPAIR_COOLDOWN", "rk_test_" + "rotated-fixture" * 4)
    second = stripe_evidence.StripeEvidence(config)
    with pytest.raises(ConnectorError): second._call(read)
    assert calls == []
    clock[0] = 121
    assert stripe_evidence.StripeEvidence(config)._call(read) == {"id": "accepted"}
    assert calls == [True]

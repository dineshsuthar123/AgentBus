from __future__ import annotations

import ipaddress
import re
import hashlib
import threading
import time
from urllib.parse import urlsplit

from agentbus.repair.live.contracts import PurchaseState, StripeConfiguration
from agentbus.repair.live.transport import ConnectorError, _PinnedConnection, credential, retry_after_seconds, resolve_addresses

_clock = time.monotonic
_cooldown_lock = threading.Lock()
_cooldowns: dict[str, float] = {}


def _http_client():
    from stripe._http_client import HTTPClient

    class ReadOnlyStripeHTTP(HTTPClient):
        name = "syndra-pinned-read-only"

        def request(self, method, url, headers, post_data=None, **_kwargs):
            parsed = urlsplit(url)
            if (method.upper() != "GET" or parsed.scheme != "https" or parsed.hostname != "api.stripe.com" or
                parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment or post_data or
                not re.fullmatch(r"/v1/(?:account|accounts/acct_[A-Za-z0-9]+|payment_intents/pi_[A-Za-z0-9]+|charges|refunds|disputes|invoices/in_[A-Za-z0-9]+(?:/lines)?|invoice_payments|subscriptions/sub_[A-Za-z0-9]+|subscription_items|entitlements/active_entitlements)", parsed.path)):
                raise ConnectorError("connector_destination")
            try:
                addresses = {item[4][0] for item in resolve_addresses(parsed.hostname, 443)}
                if not addresses or not all(ipaddress.ip_address(ip).is_global for ip in addresses):
                    raise ConnectorError("connector_destination")
                connection = _PinnedConnection(parsed.hostname, 443, sorted(addresses)[0], tls=True)
                try:
                    connection.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""), headers=dict(headers or {}))
                    response = connection.getresponse()
                    data = response.read(2_000_001)
                    if len(data) > 2_000_000 or 300 <= response.status < 400:
                        raise ConnectorError("connector_invalid")
                    if response.status >= 400:
                        # Never hand raw provider error bodies to SDK exception/logging paths.
                        code = "connector_auth" if response.status in {401, 403} else "connector_rate_limited" if response.status == 429 else "connector_unavailable"
                        raise ConnectorError(code, retry_after=retry_after_seconds(response.getheader("Retry-After", "30")) if response.status == 429 else None)
                    return data.decode("utf-8"), response.status, dict(response.getheaders())
                finally:
                    connection.finish()
            except (OSError, ValueError):
                raise ConnectorError() from None

    return ReadOnlyStripeHTTP()


class StripeEvidence:
    """Bounded API-v1 reads. No Stripe mutation method is exposed."""

    def __init__(self, config: StripeConfiguration, *, http_client=None):
        import stripe
        from stripe import _util
        # The supported SDK's opt-in debug mode prints raw response bodies before
        # user code can redact them. Refuse unsafe process logging configuration.
        if stripe.log in {"debug", "info"} or _util.STRIPE_LOG in {"debug", "info"} or _util.logger.isEnabledFor(20):
            raise ConnectorError("connector_invalid")
        key = credential(config.credential_ref)
        mode = "live" if config.livemode else "test"
        # Restricted read-only keys only; a full secret key is unnecessary authority.
        if not key.startswith(f"rk_{mode}_"):
            raise ConnectorError("connector_auth", status=403)
        self.config = config
        self._scope = hashlib.sha256(str((config.platform_account, config.connected_account, config.api_version, config.livemode)).encode()).hexdigest()
        self._deadline = _clock() + 45
        self.client = stripe.StripeClient(key, stripe_version=config.api_version,
                                         max_network_retries=0, http_client=http_client or _http_client())
        self.options = {"stripe_account": config.connected_account} if config.connected_account else {}

    def _call(self, function, *args, **kwargs):
        with _cooldown_lock:
            remaining = _cooldowns.get(self._scope, 0) - _clock()
        if remaining > 0:
            raise ConnectorError("connector_rate_limited", status=429, retry_after=max(1, int(remaining + 1)))
        if _clock() >= self._deadline:
            raise ConnectorError()
        try:
            result = function(*args, **kwargs)
            return result.to_dict()
        except ConnectorError as error:
            if error.code in {"connector_rate_limited", "connector_unavailable", "connector_auth"}:
                with _cooldown_lock:
                    if len(_cooldowns) >= 256 and self._scope not in _cooldowns:
                        expired = [key for key, deadline in _cooldowns.items() if deadline <= _clock()]
                        for key in expired: del _cooldowns[key]
                        if len(_cooldowns) >= 256:
                            raise ConnectorError("connector_rate_limited", retry_after=30) from None
                    _cooldowns[self._scope] = max(_cooldowns.get(self._scope, 0), _clock() + (error.retry_after or 5))
            raise
        except Exception:
            # Provider exceptions can contain raw bodies, customer PII or key fragments.
            raise ConnectorError("connector_invalid") from None

    def check_identity(self):
        platform = self._call(self.client.v1.accounts.retrieve_current)
        if platform.get("id") != self.config.platform_account:
            raise ConnectorError("connector_conflict")
        if self.config.connected_account:
            target = self._call(self.client.v1.accounts.retrieve_current, options=self.options)
            if target.get("id") != self.config.connected_account:
                raise ConnectorError("connector_conflict")

    def _mode(self, item):
        if item.get("livemode") is not self.config.livemode:
            raise ConnectorError("connector_conflict")

    def _pages(self, function, parameters, *, prefix_args=(), require_mode=True):
        seen, cursor = set(), None
        for _ in range(100):
            params = {**parameters, "limit": 100}
            if cursor:
                params["starting_after"] = cursor
            page = self._call(function, *prefix_args, params=params, options=self.options)
            data, more = page.get("data"), page.get("has_more")
            if not isinstance(data, list) or type(more) is not bool or len(data) > 100 or (more and not data):
                raise ConnectorError("connector_invalid")
            for item in data:
                identifier = item.get("id")
                if not isinstance(identifier, str) or identifier in seen:
                    raise ConnectorError("connector_invalid")
                seen.add(identifier)
                if require_mode:
                    self._mode(item)
                yield item
            if not more:
                return
            cursor = data[-1]["id"]
        raise ConnectorError("connector_invalid")

    def validate_purchase(self, state: PurchaseState):
        """Return a bounded reason, never a raw provider object or customer body.

        Invoice/subscription support is deliberately one unambiguous paid line and
        transaction for the original product/period. Other configurations stop.
        """
        self.check_identity()
        if not re.fullmatch(r"pi_[A-Za-z0-9]+", state.payment_id) or not state.stripe_customer:
            raise ConnectorError("connector_conflict")
        payment = self._call(self.client.v1.payment_intents.retrieve, state.payment_id, options=self.options)
        self._mode(payment)
        if (payment.get("id") != state.payment_id or payment.get("customer") != state.stripe_customer or
            payment.get("currency") != state.currency or type(payment.get("amount_received")) is not int or
            payment.get("amount_received") != state.amount_minor or payment.get("status") != "succeeded"):
            raise ConnectorError("connector_conflict")
        settled = []
        for item in self._pages(self.client.v1.charges.list, {"payment_intent": state.payment_id}):
            if item.get("payment_intent") != state.payment_id:
                raise ConnectorError("connector_conflict")
            if item.get("paid") is True and item.get("captured") is True and item.get("status") == "succeeded":
                settled.append(item)
                if len(settled) > 1:
                    raise ConnectorError("connector_conflict")
        if len(settled) != 1 or settled[0].get("amount_captured") != state.amount_minor or settled[0].get("currency") != state.currency or settled[0].get("customer") != state.stripe_customer:
            raise ConnectorError("connector_conflict")
        charge = settled[0]
        if charge.get("disputed") is not False or charge.get("amount_refunded") != state.refunded_minor:
            raise ConnectorError("connector_conflict")
        for function in (self.client.v1.refunds.list, self.client.v1.disputes.list):
            for _item in self._pages(function, {"payment_intent": state.payment_id}):
                # Any refund/dispute makes this narrow policy ineligible; stop
                # without retaining provider customer bodies or claiming absence.
                raise ConnectorError("connector_conflict")
        if state.refunded_minor:
            raise ConnectorError("connector_conflict")
        if bool(state.stripe_invoice) != bool(state.stripe_subscription):
            raise ConnectorError("connector_conflict")
        if state.stripe_invoice:
            if state.period_start.microsecond or state.period_end.microsecond:
                raise ConnectorError("connector_conflict")
            invoice = self._call(self.client.v1.invoices.retrieve, state.stripe_invoice, options=self.options)
            subscription = self._call(self.client.v1.subscriptions.retrieve, state.stripe_subscription, options=self.options)
            for item in (invoice, subscription):
                self._mode(item)
                if item.get("customer") != state.stripe_customer:
                    raise ConnectorError("connector_conflict")
            payments = self._single(self.client.v1.invoice_payments.list, {"invoice": state.stripe_invoice})
            lines = self._single(self.client.v1.invoices.line_items.list, {}, prefix_args=(state.stripe_invoice,))
            items = self._single(self.client.v1.subscription_items.list, {"subscription": state.stripe_subscription}, require_mode=False)
            if (invoice.get("status") != "paid" or invoice.get("currency") != state.currency or invoice.get("amount_paid") != state.amount_minor or
                len(payments) != 1 or payments[0].get("status") != "paid" or payments[0].get("payment", {}).get("payment_intent") != state.payment_id or
                subscription.get("status") != "active" or subscription.get("pause_collection") or subscription.get("pending_update") or
                bool(subscription.get("cancel_at_period_end")) != (state.cancellation == "period_end") or len(lines) != 1 or len(items) != 1):
                raise ConnectorError("connector_conflict")
            line, item = lines[0], items[0]
            self._mode(item.get("price", {}))
            period = line.get("period", {})
            if (not state.stripe_product or line.get("pricing", {}).get("price_details", {}).get("product") != state.stripe_product or
                item.get("price", {}).get("product") != state.stripe_product or line.get("currency") != state.currency or
                line.get("amount") != state.amount_minor or item.get("current_period_start") != int(state.period_start.timestamp()) or
                item.get("current_period_end") != int(state.period_end.timestamp()) or
                period != {"start": int(state.period_start.timestamp()), "end": int(state.period_end.timestamp())} or
                line.get("parent", {}).get("subscription_item_details", {}).get("subscription") != state.stripe_subscription):
                raise ConnectorError("connector_conflict")
        if self.config.use_entitlements:
            found = False
            for item in self._pages(self.client.v1.entitlements.active_entitlements.list, {"customer": state.stripe_customer}):
                found = found or item.get("feature") == state.stripe_feature
            if not state.stripe_feature or not found:
                raise ConnectorError("connector_conflict")
        return "Stripe account, mode, transaction and complete bounded pages agree. External race coordination still requires application support."

    def _single(self, function, parameters, **kwargs):
        values = []
        for item in self._pages(function, parameters, **kwargs):
            values.append(item)
            if len(values) > 1:
                raise ConnectorError("connector_conflict")
        return values

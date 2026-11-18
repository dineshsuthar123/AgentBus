CREATE TABLE IF NOT EXISTS syndra_integration_version(version integer PRIMARY KEY CHECK(version = 1));
INSERT INTO syndra_integration_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS syndra_purchase (
    tenant text NOT NULL, purchase_id text NOT NULL, revision bigint NOT NULL CHECK(revision > 0),
    state jsonb NOT NULL, PRIMARY KEY(tenant, purchase_id)
);
CREATE TABLE IF NOT EXISTS syndra_plan (
    plan_id text PRIMARY KEY, digest text NOT NULL, principal text NOT NULL,
    payload jsonb NOT NULL, revoked boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS syndra_purchase_payment
    ON syndra_purchase(tenant, (state->>'payment_id'));
CREATE TABLE IF NOT EXISTS syndra_receipt (
    operation_id text PRIMARY KEY, payload_digest text NOT NULL, logical_effect text NOT NULL,
    tenant text NOT NULL, purchase_id text NOT NULL, payload jsonb NOT NULL,
    FOREIGN KEY(tenant, purchase_id) REFERENCES syndra_purchase(tenant, purchase_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS syndra_one_effect
    ON syndra_receipt(logical_effect) WHERE payload->>'outcome' = 'committed';
CREATE INDEX IF NOT EXISTS syndra_receipt_purchase ON syndra_receipt(tenant, purchase_id);
CREATE TABLE IF NOT EXISTS syndra_entitlement (
    tenant text NOT NULL, account_id text NOT NULL, feature text NOT NULL,
    purchase_id text NOT NULL, period_start timestamptz NOT NULL, period_end timestamptz NOT NULL,
    present boolean NOT NULL, revision bigint NOT NULL,
    PRIMARY KEY(tenant, purchase_id),
    FOREIGN KEY(tenant, purchase_id) REFERENCES syndra_purchase(tenant, purchase_id),
    CHECK(period_start < period_end)
);
CREATE INDEX IF NOT EXISTS syndra_access_lookup ON syndra_entitlement(tenant, account_id, feature, period_end);
CREATE TABLE IF NOT EXISTS syndra_business_action (
    action_id text PRIMARY KEY, tenant text NOT NULL, purchase_id text NOT NULL,
    kind text NOT NULL, revision bigint NOT NULL, occurred_at timestamptz NOT NULL,
    FOREIGN KEY(tenant, purchase_id) REFERENCES syndra_purchase(tenant, purchase_id)
);
CREATE TABLE IF NOT EXISTS syndra_outbox (
    event_id text PRIMARY KEY, tenant text NOT NULL, purchase_id text NOT NULL,
    revision bigint NOT NULL, delivered boolean NOT NULL DEFAULT false,
    FOREIGN KEY(tenant, purchase_id) REFERENCES syndra_purchase(tenant, purchase_id),
    UNIQUE(tenant, purchase_id, revision)
);
CREATE INDEX IF NOT EXISTS syndra_pending_outbox ON syndra_outbox(event_id) WHERE NOT delivered;
CREATE TABLE IF NOT EXISTS syndra_access_projection (
    tenant text NOT NULL, purchase_id text NOT NULL, revision bigint NOT NULL,
    present boolean NOT NULL, PRIMARY KEY(tenant, purchase_id),
    FOREIGN KEY(tenant, purchase_id) REFERENCES syndra_purchase(tenant, purchase_id)
);
CREATE OR REPLACE FUNCTION syndra_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Operation receipts and business action records are immutable';
END;
$$;
CREATE OR REPLACE TRIGGER syndra_receipt_immutable BEFORE UPDATE OR DELETE ON syndra_receipt
    FOR EACH ROW EXECUTE FUNCTION syndra_receipt_immutable();
CREATE OR REPLACE TRIGGER syndra_action_immutable BEFORE UPDATE OR DELETE ON syndra_business_action
    FOR EACH ROW EXECUTE FUNCTION syndra_receipt_immutable();
CREATE OR REPLACE FUNCTION syndra_plan_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Approval tombstones are retained';
    END IF;
    IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.digest <> OLD.digest
       OR NEW.principal <> OLD.principal OR (OLD.revoked AND NOT NEW.revoked) THEN
        RAISE EXCEPTION 'Approval scope and revocation are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER syndra_plan_guard BEFORE UPDATE OR DELETE ON syndra_plan
    FOR EACH ROW EXECUTE FUNCTION syndra_plan_guard();

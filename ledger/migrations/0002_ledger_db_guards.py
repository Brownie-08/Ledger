"""Database-level guards: append-only ledger + deferred zero-sum check.

Model save()/delete() guards can be bypassed with queryset .update()/.delete()
or raw SQL; these triggers cannot.
"""

from django.db import migrations

FORWARD = r"""
CREATE OR REPLACE FUNCTION ledger_entries_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entry is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entry_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_immutable();

CREATE OR REPLACE FUNCTION ledger_transaction_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ledger_transaction rows cannot be deleted';
    END IF;
    IF OLD.status <> 'pending' THEN
        IF NEW.type IS DISTINCT FROM OLD.type
           OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
           OR NEW.reference IS DISTINCT FROM OLD.reference
           OR NEW.metadata IS DISTINCT FROM OLD.metadata
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR (OLD.reverses_id IS NOT NULL AND NEW.reverses_id IS DISTINCT FROM OLD.reverses_id)
           OR NOT (NEW.status = OLD.status OR (OLD.status = 'posted' AND NEW.status = 'reversed'))
        THEN
            RAISE EXCEPTION 'posted ledger_transaction % is immutable', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_transaction_guard
    BEFORE UPDATE OR DELETE ON ledger_transaction
    FOR EACH ROW EXECUTE FUNCTION ledger_transaction_guard();

CREATE OR REPLACE FUNCTION ledger_check_balanced() RETURNS trigger AS $$
DECLARE
    bad RECORD;
BEGIN
    SELECT currency, SUM(amount) AS total INTO bad
      FROM ledger_entry WHERE transaction_id = NEW.transaction_id
     GROUP BY currency HAVING SUM(amount) <> 0 LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'transaction % is unbalanced in % by %', NEW.transaction_id, bad.currency, bad.total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ledger_entry_balanced
    AFTER INSERT ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_check_balanced();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_entry;
DROP TRIGGER IF EXISTS ledger_transaction_guard ON ledger_transaction;
DROP TRIGGER IF EXISTS ledger_entry_no_update_delete ON ledger_entry;
DROP FUNCTION IF EXISTS ledger_check_balanced();
DROP FUNCTION IF EXISTS ledger_transaction_guard();
DROP FUNCTION IF EXISTS ledger_entries_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]

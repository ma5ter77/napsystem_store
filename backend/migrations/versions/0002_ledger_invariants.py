"""Database-enforced financial invariants: immutable balanced journal, append-only audit.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION nap_forbid_mutation() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'append-only table %: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
    END $$ LANGUAGE plpgsql;

    CREATE TRIGGER ledger_tx_immutable BEFORE UPDATE OR DELETE ON ledger_transactions
      FOR EACH ROW EXECUTE FUNCTION nap_forbid_mutation();
    CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries
      FOR EACH ROW EXECUTE FUNCTION nap_forbid_mutation();
    CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_log
      FOR EACH ROW EXECUTE FUNCTION nap_forbid_mutation();

    -- every transaction must balance to zero per currency at COMMIT time
    CREATE OR REPLACE FUNCTION nap_check_balanced() RETURNS trigger AS $$
    DECLARE s bigint; c int;
    BEGIN
      SELECT COALESCE(SUM(amount_minor),0), COUNT(DISTINCT currency) INTO s, c
        FROM ledger_entries WHERE transaction_id = NEW.transaction_id;
      IF s <> 0 THEN
        RAISE EXCEPTION 'ledger transaction % is not balanced (sum=%)', NEW.transaction_id, s;
      END IF;
      IF c > 1 THEN
        RAISE EXCEPTION 'ledger transaction % mixes currencies', NEW.transaction_id;
      END IF;
      RETURN NULL;
    END $$ LANGUAGE plpgsql;

    CREATE CONSTRAINT TRIGGER ledger_balanced AFTER INSERT ON ledger_entries
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nap_check_balanced();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS ledger_balanced ON ledger_entries;
    DROP TRIGGER IF EXISTS audit_immutable ON audit_log;
    DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries;
    DROP TRIGGER IF EXISTS ledger_tx_immutable ON ledger_transactions;
    DROP FUNCTION IF EXISTS nap_check_balanced();
    DROP FUNCTION IF EXISTS nap_forbid_mutation();
    """)

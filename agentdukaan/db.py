"""SQLite storage (WAL mode). Fresh connection per operation = thread-safe.

Schema notes:
  - audit_log is APPEND-ONLY, enforced by triggers (no UPDATE / no DELETE).
  - orders.idempotency_key is UNIQUE: the database itself rejects double-creates.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  product_id       TEXT PRIMARY KEY,
  sku              TEXT UNIQUE NOT NULL,
  name             TEXT NOT NULL,
  brand            TEXT NOT NULL,
  category         TEXT NOT NULL,
  description      TEXT NOT NULL,             -- UNTRUSTED content (see injection firewall)
  unit_price_paise INTEGER NOT NULL,          -- GST-inclusive
  mrp_paise        INTEGER NOT NULL,
  gst_rate_bps     INTEGER NOT NULL,          -- 1800 == 18.00%
  rating_bps       INTEGER NOT NULL,          -- 4600 == 4.60
  stock            INTEGER NOT NULL CHECK (stock >= 0),
  tags             TEXT NOT NULL DEFAULT '',
  emoji            TEXT NOT NULL DEFAULT '🛒',
  active           INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
  quote_id        TEXT PRIMARY KEY,
  pincode         TEXT NOT NULL,
  items_json      TEXT NOT NULL,
  subtotal_paise  INTEGER NOT NULL,
  gst_paise       INTEGER NOT NULL,
  shipping_paise  INTEGER NOT NULL,
  total_paise     INTEGER NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open',   -- open | consumed | expired
  created_at      TEXT NOT NULL,
  expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  order_id            TEXT PRIMARY KEY,
  quote_id            TEXT NOT NULL REFERENCES quotes(quote_id),
  items_json          TEXT NOT NULL,
  total_paise         INTEGER NOT NULL,
  status              TEXT NOT NULL DEFAULT 'created',
      -- created | awaiting_approval | pending_payment | paid | rejected
  idempotency_key     TEXT UNIQUE,
  mission_id          TEXT,
  razorpay_order_id   TEXT,
  razorpay_payment_id TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id  TEXT PRIMARY KEY,
  order_id     TEXT NOT NULL REFERENCES orders(order_id),
  amount_paise INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired
  reason       TEXT NOT NULL DEFAULT '',
  requested_at TEXT NOT NULL,
  decided_at   TEXT,
  decided_by   TEXT,
  expires_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buyers (
  buyer_id           TEXT PRIMARY KEY,
  display_name       TEXT NOT NULL,
  daily_budget_paise INTEGER NOT NULL,
  spent_today_paise  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS missions (
  mission_id    TEXT PRIMARY KEY,
  buyer_id      TEXT NOT NULL REFERENCES buyers(buyer_id),
  brief         TEXT NOT NULL,
  budget_paise  INTEGER,
  spent_paise   INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  actor        TEXT NOT NULL,   -- agent | human | system | policy
  plane        TEXT NOT NULL,   -- merchant | trust | buyer
  action       TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  decision     TEXT NOT NULL,   -- ok | blocked | pending | error
  detail_json  TEXT NOT NULL
);

-- The audit ledger is append-only. Enforced at the database level.
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    """Yield a connection. Autocommit mode; use BEGIN IMMEDIATE for write races."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    except Exception:
        try:
            c.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass  # no active transaction — nothing to roll back
        raise
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)

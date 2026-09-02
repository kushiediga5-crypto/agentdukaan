"""Central configuration. Every knob is env-overridable; defaults are demo-safe.

Money is ALWAYS integer paise (₹1 = 100 paise). Floats never touch money.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Tiny .env loader (no dependency). Real env vars always win."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


_load_dotenv()


def _rupees_env(name: str, default_rupees: int) -> int:
    """Read a rupee-denominated env var, return paise."""
    try:
        return int(float(os.environ.get(name, default_rupees)) * 100)
    except ValueError:
        return default_rupees * 100


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Storage ---------------------------------------------------------------
    db_path: Path = Path(
        os.environ.get("AGENTDUKAAN_DB_PATH", ROOT / "data" / "agentdukaan.db")
    )

    # Trust plane -----------------------------------------------------------
    # 0 => EVERY payment requires explicit human approval (strongest demo).
    approval_threshold_paise: int = _rupees_env("APPROVAL_THRESHOLD_RUPEES", 0)
    per_txn_cap_paise: int = _rupees_env("PER_TXN_CAP_RUPEES", 10_000)
    daily_budget_paise: int = _rupees_env("DAILY_BUDGET_RUPEES", 25_000)
    quote_ttl_seconds: int = _int_env("QUOTE_TTL_SECONDS", 600)
    approval_ttl_seconds: int = _int_env("APPROVAL_TTL_SECONDS", 300)
    # Quote must match live catalog prices EXACTLY at order time (anti price-drift).
    price_drift_tolerance_paise: int = _rupees_env("PRICE_DRIFT_TOLERANCE_RUPEES", 0)

    # Gateway ---------------------------------------------------------------
    razorpay_key_id: str = os.environ.get("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = os.environ.get("RAZORPAY_KEY_SECRET", "")

    # Buyer plane (agent) ----------------------------------------------------
    agent_mcp_url: str = os.environ.get("AGENT_MCP_URL", "http://127.0.0.1:8001/mcp")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")

    # Network ---------------------------------------------------------------
    http_host: str = os.environ.get("HTTP_HOST", "0.0.0.0")
    http_port: int = _int_env("HTTP_PORT", 8000)
    mcp_host: str = os.environ.get("MCP_HOST", "0.0.0.0")
    mcp_port: int = _int_env("MCP_PORT", 8001)
    approval_base_url: str = os.environ.get(
        "APPROVAL_BASE_URL", "http://localhost:8000"
    )

    @property
    def gateway_is_live(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


settings = Settings()

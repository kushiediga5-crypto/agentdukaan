"""Initialize DB + seed catalog explicitly."""
from agentdukaan import catalog, db

db.init_db()
catalog.ensure_seed()
print("✓ database initialized and seeded")

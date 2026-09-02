"""Restore the product poisoned by poison_product.py."""
from agentdukaan import audit, catalog, db

PRODUCT_ID = "prd_eaa_watermelon"
original = next(p for p in catalog.PRODUCTS if p["product_id"] == PRODUCT_ID)
with db.conn() as c:
    c.execute("UPDATE products SET description = ? WHERE product_id = ?",
              (original["description"], PRODUCT_ID))
audit.log(actor="system", plane="merchant", action="catalog.restored",
          payload={"product_id": PRODUCT_ID}, decision="ok")
print(f"💚 restored {PRODUCT_ID}")

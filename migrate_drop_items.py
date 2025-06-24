# migrate_drop_items.py
from app import app, db
from sqlalchemy import text

with app.app_context():
    # Drop the old JSON column
    db.session.execute(text("ALTER TABLE reagent_order DROP COLUMN items;"))
    db.session.commit()
    print("Dropped reagent_order.items column")

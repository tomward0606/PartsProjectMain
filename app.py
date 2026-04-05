# ──────────────────────────────────────────────────────────────────────────────
# Servitech Parts ORDER SYSTEM 
# ──────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta
import os
import csv
from io import StringIO

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response, Response
)
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from typing import Set, Dict, List
from sqlalchemy import desc, func, UniqueConstraint, cast
from sqlalchemy.types import Integer
from sqlalchemy.exc import IntegrityError


# ── App & Config ──────────────────────────────────────────────────────────────
app = Flask(__name__)

# Simple dev secret key
app.secret_key = "fallback-dev-key"

# Database: hard-coded Render external URL (requires SSL)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    "postgresql://servitech_db_user:"
    "79U6KaAxlHdUfOeEt1iVDc65KXFLPie2"
    "@dpg-d1ckf9ur433s73fti9p0-a.oregon-postgres.render.com"
    "/servitech_db?sslmode=require"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# PRODUCTION: Mail configuration should use environment variables
# For production, these should be loaded from environment variables instead of hardcoded
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "servitech.stock@gmail.com"
app.config["MAIL_PASSWORD"] = "qmorqthzpbxqnkrp"  # In production, use environment variable
app.config["MAIL_DEFAULT_SENDER"] = ("Servitech Stock", "servitech.stock@gmail.com")

# Production settings - ensure emails are sent
app.config["TESTING"] = False
app.config["MAIL_SUPPRESS_SEND"] = False
app.config["MAIL_DEBUG"] = False  # Set to True for debugging, False for production
app.config["MAIL_FAIL_SILENTLY"] = False

# Production logging configuration
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

# NOW create the database and mail objects AFTER all config is set
db = SQLAlchemy(app)
mail = Mail(app)

# ── Models ────────────────────────────────────────────────────────────────────
# Reagents (existing)
class ReagentOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    items = db.relationship("ReagentOrderItem", backref="order", cascade="all, delete-orphan")

class ReagentOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("reagent_order.id"), nullable=False)
    part_number = db.Column(db.String(64))
    description = db.Column(db.String(256))
    quantity = db.Column(db.Integer)

# Parts Orders (engineer-submitted)
class PartsOrder(db.Model):
    __tablename__ = "parts_order"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), nullable=True)
    items = db.relationship("PartsOrderItem", backref="order", cascade="all, delete-orphan")

class PartsOrderItem(db.Model):
    __tablename__ = "parts_order_item"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("parts_order.id"), nullable=False)
    part_number = db.Column(db.String(64))
    description = db.Column(db.String(256))
    quantity = db.Column(db.Integer)
    quantity_sent = db.Column(db.Integer, default=0)
    back_order = db.Column(db.Boolean, nullable=False, default=False)

# Dispatch tables (from Stock System) — needed for "Sent last 7 days"
class DispatchNote(db.Model):
    __tablename__ = "dispatch_note"
    id = db.Column(db.Integer, primary_key=True)
    engineer_email = db.Column(db.String(120), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    picker_name = db.Column(db.String(100), nullable=True)
    items = db.relationship("DispatchItem", backref="dispatch_note", cascade="all, delete-orphan")

class DispatchItem(db.Model):
    __tablename__ = "dispatch_item"
    id = db.Column(db.Integer, primary_key=True)
    dispatch_note_id = db.Column(db.Integer, db.ForeignKey("dispatch_note.id"), nullable=False)
    part_number = db.Column(db.String(64))
    quantity_sent = db.Column(db.Integer)
    description = db.Column(db.String(256))

class HiddenPart(db.Model):
    __tablename__ = "hidden_part"
    part_number = db.Column(db.String, primary_key=True)

#-- helper for hiding parts (not currently used)

PART_NUMBER_KEY = "Product Code"  # match your CSV header exactly

def norm_pn(s: str) -> str:
    return (s or "").strip().upper()

def get_hidden_part_numbers() -> Set[str]:
    rows = db.session.query(HiddenPart.part_number).all()
    return {norm_pn(pn) for (pn,) in rows}



# ── Stocktake Models ─────────────────────────────────────────────────────────

class StocktakeRun(db.Model):
    __tablename__ = "stocktake_run"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Stocktake(db.Model):
    __tablename__ = "stocktake"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("stocktake_run.id"), nullable=False)
    engineer_email = db.Column(db.String(120), nullable=False)
    # draft (pending) | submitted | checked
    status = db.Column(db.String(20), default="draft", nullable=False)
    submitted_at = db.Column(db.DateTime, nullable=True)
    # Admin check-off
    checked_by = db.Column(db.String(120), nullable=True)
    checked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    run = db.relationship("StocktakeRun", backref="stocktakes")
    items = db.relationship("StocktakeItem", backref="stocktake", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("run_id", "engineer_email", name="uq_stocktake_run_engineer"),
    )


class StocktakeItem(db.Model):
    __tablename__ = "stocktake_item"
    id = db.Column(db.Integer, primary_key=True)
    stocktake_id = db.Column(db.Integer, db.ForeignKey("stocktake.id"), nullable=False)
    part_number = db.Column(db.String(64), nullable=False)
    description = db.Column(db.String(256), nullable=True)
    quantity = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("stocktake_id", "part_number", name="uq_stocktake_item_unique_part"),
    )


class StocktakeUnfoundItem(db.Model):
    __tablename__ = "stocktake_unfound_item"
    id = db.Column(db.Integer, primary_key=True)
    stocktake_id = db.Column(db.Integer, db.ForeignKey("stocktake.id"), nullable=False)
    part_code = db.Column(db.String(64), nullable=False)
    description = db.Column(db.String(256), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    stocktake = db.relationship("Stocktake", backref="unfound_items")


with app.app_context():
    try:
        db.create_all()
    except Exception as ex:
        logger.warning(f"Database auto-create skipped: {ex}")



# ── CSV catalogue load ────────────────────────────────────────────────────────
PARTS_CSV_PATH = "parts.csv"
ALLOWED_COLOURS = ["Green", "Yellow", "Red", "Purple"]
DEFAULT_STOCKTAKE_RUN_NAME = "April 2026 Stocktake"


def normalize_colour(colour: str) -> str:
    raw = (colour or "").strip().lower()
    if raw in {"green", "yellow", "amber", "red", "purple"}:
        if raw == "amber":
            return "Yellow"
        return raw.capitalize()
    return ""


def normalize_installs(value: str) -> bool:
    return (value or "").strip().lower() in {"yes", "y", "true", "1"}


def part_to_csv_row(part: Dict[str, object]) -> Dict[str, str]:
    return {
        "Product Code": str(part.get("part_number", "") or "").strip(),
        "Description": str(part.get("description", "") or "").strip(),
        "Category": str(part.get("category", "") or "").strip(),
        "Make": str(part.get("make", "") or "").strip(),
        "Manufacturer": str(part.get("manufacturer", "") or "").strip(),
        "image": str(part.get("image", "") or "").strip(),
        "Colour": normalize_colour(str(part.get("colour", "") or "")),
        "Installs": "Yes" if bool(part.get("installs")) else "",
    }


def load_parts_catalogue() -> List[Dict[str, object]]:
    loaded_parts: List[Dict[str, object]] = []
    with open(PARTS_CSV_PATH, newline="", encoding="cp1252") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            part_number = (row.get("Product Code") or "").strip().replace("\u00a0", "").replace("\r", "").replace("\n", "")
            if not part_number:
                continue

            clean_filename = (row.get("image") or "").strip() or (part_number.replace("/", "") + ".png")

            loaded_parts.append({
                "part_number": part_number,
                "description": (row.get("Description") or "").strip(),
                "category": (row.get("Category") or "").strip(),
                "make": (row.get("Make") or "").strip(),
                "manufacturer": (row.get("Manufacturer") or "").strip(),
                "image": clean_filename,
                "colour": normalize_colour(row.get("Colour", "")),
                "installs": normalize_installs(row.get("Installs", "")),
            })
    return loaded_parts


def save_parts_catalogue(parts: List[Dict[str, object]]):
    fieldnames = ["Product Code", "Description", "Category", "Make", "Manufacturer", "image", "Colour", "Installs"]
    with open(PARTS_CSV_PATH, "w", newline="", encoding="cp1252") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for part in parts:
            writer.writerow(part_to_csv_row(part))


parts_db = load_parts_catalogue()


def refresh_parts_catalogue():
    global parts_db
    parts_db = load_parts_catalogue()


def get_categories(parts):
    return sorted({p["category"] for p in parts if p.get("category")})


def get_part_by_number(part_number: str):
    return next((p for p in parts_db if p["part_number"] == part_number), None)


def get_part_colour(part_number: str) -> str:
    part = get_part_by_number(part_number)
    return (part or {}).get("colour", "") or ""


def enrich_stocktake_items_with_colour(items):
    enriched = []
    for item in items:
        enriched.append({
            "part_number": item.part_number,
            "description": item.description,
            "quantity": int(item.quantity or 0),
            "colour": get_part_colour(item.part_number),
            "is_unfound": False,
        })
    return enriched


def get_stocktake_rows_with_unfound(stocktake_id: int):
    regular_items = (
        StocktakeItem.query
        .filter_by(stocktake_id=stocktake_id)
        .order_by(StocktakeItem.part_number.asc())
        .all()
    )
    rows = enrich_stocktake_items_with_colour(regular_items)

    unfound_items = (
        StocktakeUnfoundItem.query
        .filter_by(stocktake_id=stocktake_id)
        .order_by(StocktakeUnfoundItem.created_at.asc(), StocktakeUnfoundItem.id.asc())
        .all()
    )
    for uf in unfound_items:
        rows.append({
            "part_number": uf.part_code,
            "description": uf.description,
            "quantity": int(uf.quantity or 0),
            "colour": "",
            "is_unfound": True,
        })
    return rows

def stocktake_leader_password() -> str:
    # TODO: replace with env var later ------------------------------------------------------STOCKTAKE PASSOWRRD
    return "123"


# ── Helpers for "My Orders" ───────────────────────────────────────────────────
def get_recent_dispatches(email: str, days: int = 7):
    """Actual picked lines for this engineer in the last N days (from dispatch tables)."""
    if not email:
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.session.query(
            DispatchNote.date.label("dispatch_date"),
            DispatchItem.part_number.label("part_number"),
            DispatchItem.description.label("description"),
            DispatchItem.quantity_sent.label("qty_sent"),
        )
        .join(DispatchItem, DispatchItem.dispatch_note_id == DispatchNote.id)
        .filter(
            func.lower(DispatchNote.engineer_email) == email.lower(),
            DispatchNote.date >= cutoff
        )
        .order_by(DispatchNote.date.desc(), DispatchItem.id.desc())
        .all()
    )
    return rows


def get_last_dispatch_map(email: str):
    """Map part_number -> latest dispatch date for this engineer (None if never dispatched)."""
    if not email:
        return {}
    sub = (
        db.session.query(
            DispatchItem.part_number.label("pn"),
            func.max(DispatchNote.date).label("last_date"),
        )
        .join(DispatchNote, DispatchNote.id == DispatchItem.dispatch_note_id)
        .filter(func.lower(DispatchNote.engineer_email) == email.lower())
        .group_by(DispatchItem.part_number)
        .subquery()
    )
    return {row.pn: row.last_date for row in db.session.query(sub).all()}

def get_older_dispatches(email: str, older_than_days: int = 7):
    """Actual picked lines older than N days (from dispatch tables)."""
    if not email:
        return []
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    rows = (
        db.session.query(
            DispatchNote.date.label("dispatch_date"),
            DispatchItem.part_number.label("part_number"),
            DispatchItem.description.label("description"),
            DispatchItem.quantity_sent.label("qty_sent"),
        )
        .join(DispatchItem, DispatchItem.dispatch_note_id == DispatchNote.id)
        .filter(
            func.lower(DispatchNote.engineer_email) == email.lower(),
            DispatchNote.date < cutoff
        )
        .order_by(DispatchNote.date.desc(), DispatchItem.id.desc())
        .all()
    )
    return rows


# ── Stocktake Helpers ─────────────────────────────────────────────────────────

def get_or_create_active_stocktake_run() -> StocktakeRun:
    run = StocktakeRun.query.filter_by(is_active=True).order_by(StocktakeRun.id.desc()).first()
    if run:
        return run

    existing_target = (
        StocktakeRun.query
        .filter_by(name=DEFAULT_STOCKTAKE_RUN_NAME)
        .order_by(StocktakeRun.id.desc())
        .first()
    )
    if existing_target:
        existing_target.is_active = True
        db.session.commit()
        return existing_target

    run = StocktakeRun(name=DEFAULT_STOCKTAKE_RUN_NAME, is_active=True)
    db.session.add(run)
    db.session.commit()
    return run


def migrate_active_stocktake_run_to_april_2026():
    current_active = StocktakeRun.query.filter_by(is_active=True).order_by(StocktakeRun.id.desc()).first()
    april_run = StocktakeRun.query.filter_by(name=DEFAULT_STOCKTAKE_RUN_NAME).order_by(StocktakeRun.id.desc()).first()

    if april_run:
        StocktakeRun.query.update({"is_active": False})
        april_run.is_active = True
        db.session.commit()
        return

    if current_active and current_active.name != DEFAULT_STOCKTAKE_RUN_NAME:
        current_active.is_active = False

    new_run = StocktakeRun(name=DEFAULT_STOCKTAKE_RUN_NAME, is_active=True)
    db.session.add(new_run)
    db.session.commit()


def stocktake_counts(stocktake_id: int):
    """Return (lines_count, total_qty) for a stocktake."""
    items = StocktakeItem.query.filter_by(stocktake_id=stocktake_id).all()
    unfound_items = StocktakeUnfoundItem.query.filter_by(stocktake_id=stocktake_id).all()
    lines = len(items) + len(unfound_items)
    total_qty = sum(int(getattr(i, "quantity", 0) or 0) for i in items) + sum(int(getattr(i, "quantity", 0) or 0) for i in unfound_items)
    return lines, total_qty

def stocktake_leader_email() -> str:
    # Configure in environment on Render
    return (os.environ.get("STOCKTAKE_LEADER_EMAIL") or "servitech.stock@gmail.com").strip()

def normalize_engineer_email(user: str) -> str:
    candidate = (user or "").strip().lower()
    if not candidate:
        return ""
    # Require full email so we avoid silently auto-appending domains.
    if "@" not in candidate:
        return ""
    if not candidate.endswith("@servitech.co.uk"):
        return ""
    return candidate


def normalize_order_email(user: str) -> str:
    """Basket/reorder helper: allow short prefix and append Servitech domain."""
    candidate = (user or "").strip().lower()
    if not candidate:
        return ""
    if "@" not in candidate:
        candidate = f"{candidate}@servitech.co.uk"
    if not candidate.endswith("@servitech.co.uk"):
        return ""
    return candidate


with app.app_context():
    try:
        migrate_active_stocktake_run_to_april_2026()
    except Exception as ex:
        logger.warning(f"Stocktake run migration skipped: {ex}")


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def landing():
    return render_template("landing.html")

# Test route to verify email configuration
@app.route("/test-email")
def test_email():
    """Test route to verify email configuration is working"""
    try:
        logger.info("Testing email configuration...")
        logger.info(f"MAIL_SERVER: {app.config.get('MAIL_SERVER')}")
        logger.info(f"MAIL_PORT: {app.config.get('MAIL_PORT')}")
        logger.info(f"MAIL_USE_TLS: {app.config.get('MAIL_USE_TLS')}")
        logger.info(f"MAIL_USERNAME: {app.config.get('MAIL_USERNAME')}")
        logger.info(f"TESTING: {app.config.get('TESTING')}")
        logger.info(f"MAIL_SUPPRESS_SEND: {app.config.get('MAIL_SUPPRESS_SEND')}")
        
        msg = Message(
            subject="TEST EMAIL - Reagent System",
            recipients=["Purchasing@servitech.co.uk"],
            cc=["tom@servitech.co.uk"],
            body="This is a test email to verify the reagent ordering system is working properly.\n\nIf you receive this, the email configuration is working correctly."
        )
        mail.send(msg)
        logger.info("Test email sent successfully!")
        return "Test email sent successfully! Check Michelle's inbox and spam folder, and check application logs for SMTP details."
    except Exception as e:
        logger.error(f"Test email failed: {str(e)}")
        return f"Test email failed to send. Error: {str(e)}"

# Catalogue (Parts)
@app.route("/catalogue")
def index():
    engineer_role = (request.args.get("role") or "service").strip().lower()
    if engineer_role not in {"service", "installs"}:
        engineer_role = "service"
    session["parts_portal_role"] = engineer_role

    # 1) Base list: non-reagents from parts_db
    base = [p for p in parts_db if "reagent" not in (p.get("category", "").lower())]

    if engineer_role == "installs":
        base = [p for p in base if bool(p.get("installs"))]

    # 2) Exclude hidden product codes
    hidden = get_hidden_part_numbers()  # set[str], uppercased
    parts = [p for p in base if norm_pn(p.get("part_number", "")) not in hidden]

    # 3) Existing filters
    category = request.args.get("category")
    search = (request.args.get("search") or "").strip().lower()

    if search:
        filtered = [
            p for p in parts
            if (not category or p.get("category") == category) and (
                search in (p.get("part_number", "").lower()) or
                search in (p.get("description", "").lower()) or
                search in (p.get("make", "").lower()) or
                search in (p.get("manufacturer", "").lower())
            )
        ]
    else:
        filtered = [p for p in parts if not category or p.get("category") == category]

    return render_template(
        "index.html",
        parts=filtered,
        categories=get_categories(parts),  # categories built from visible items
        selected_category=category,
        search=search,
        engineer_role=engineer_role,
    )


# Catalogue (Reagents)
@app.route("/reagents")
def reagents():
    # 1) Base list: reagents only
    base = [p for p in parts_db if "reagent" in (p.get("category", "").lower())]

    # 2) Exclude hidden product codes
    hidden = get_hidden_part_numbers()
    parts = [p for p in base if norm_pn(p.get("part_number", "")) not in hidden]

    # 3) Existing search
    search = (request.args.get("search") or "").strip().lower()
    if search:
        filtered = [
            p for p in parts if (
                search in (p.get("part_number", "").lower()) or
                search in (p.get("description", "").lower()) or
                search in (p.get("make", "").lower()) or
                search in (p.get("manufacturer", "").lower())
            )
        ]
    else:
        filtered = parts

    return render_template("reagents.html", parts=filtered, search=search)

# Basket views
@app.route("/parts_basket")
def view_parts_basket():
    return render_template(
        "parts_basket.html",
        basket=session.get("basket", {}),
        engineer_role=session.get("parts_portal_role", "service")
    )

@app.route("/reagents_basket")
def view_reagents_basket():
    return render_template("reagents_basket.html", basket=session.get("basket", {}))

# Basket actions
@app.route("/add_to_basket/<path:part_number>")
def add_to_basket(part_number):
    part = get_part_by_number(part_number)
    if not part:
        return redirect(url_for("index"))

    role = session.get("parts_portal_role", "service")
    is_reagent = "reagent" in (part.get("category", "").lower())
    if role == "installs" and not is_reagent and not bool(part.get("installs")):
        flash("This part is not available in the installs parts catalogue.", "warning")
        return redirect(request.referrer or url_for("index", role=role))

    basket = session.get("basket", {})
    if part_number in basket:
        basket[part_number]["quantity"] += 1
    else:
        basket[part_number] = {
            "description": part["description"],
            "category": part["category"],
            "make": part["make"],
            "manufacturer": part["manufacturer"],
            "image": part["image"],
            "colour": part.get("colour", ""),
            "quantity": 1,
        }
    session["basket"] = basket

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return ("", 204)
    return redirect(request.referrer or url_for("index"))

@app.route("/remove_from_basket/<path:part_number>")
def remove_from_basket(part_number):
    basket = session.get("basket", {})
    basket.pop(part_number, None)
    session["basket"] = basket

    ref = request.referrer or ""
    if "reagents_basket" in ref:
        return redirect(url_for("view_reagents_basket"))
    return redirect(url_for("view_parts_basket"))

@app.route("/update_quantity/<path:part_number>", methods=["POST"])
def update_quantity(part_number):
    try:
        new_qty = int(request.form.get("quantity", 0))
    except ValueError:
        new_qty = 0

    basket = session.get("basket", {})
    if new_qty <= 0:
        basket.pop(part_number, None)
    elif part_number in basket:
        basket[part_number]["quantity"] = new_qty

    session["basket"] = basket

    ref = request.referrer or ""
    return redirect(url_for("view_reagents_basket" if "reagents_basket" in ref else "view_parts_basket"))

# Submit basket (sends email + writes PartsOrder/ReagentOrder)
@app.route("/submit_basket", methods=["POST"])
def submit_basket():
    engineer_name = request.form["email_user"].strip()
    engineer_email = normalize_order_email(engineer_name)
    source = request.form.get("source", "catalogue")
    basket = session.get("basket", {})
    engineer_role = session.get("parts_portal_role", "service")

    if not basket or not engineer_email:
        flash("Your basket is empty or email missing", "warning")
        return redirect(url_for("view_reagents_basket" if source == "reagents" else "view_parts_basket"))

    # email body
    lines = [
        (
            f"• Part Number: {pnum}\n"
            f"  Description: {item['description']}\n"
            f"  Quantity: {item['quantity']}\n"
            f"  Colour: {(item.get('colour') or 'Not set')}"
        )
        for pnum, item in basket.items()
    ]
    comments = request.form.get("comments", "").strip()
    if comments:
        lines.append("\nComments:\n" + comments)

    body_text = (
        f"Engineer: {engineer_email}\n"
        f"Engineer Type: {engineer_role.capitalize()}\n"
        f"Request Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "Requested Items:\n\n" + "\n\n".join(lines) + "\n"
    )

    # Determine recipients and subject based on source
    if source == "reagents":
        subject = f"REAGENTS REQUEST FROM {engineer_email}"
        recipients = ["Purchasing@servitech.co.uk"]
        logger.info(f"Sending reagent email to Purchasing: {recipients}")
    else:
        subject = f"PARTS REQUEST FROM {engineer_email}"
        recipients = ["StockRequests@servitech.co.uk"]
        logger.info(f"Sending parts email to: {recipients}")

    # Enhanced email sending with error handling
    try:
        logger.info(f"Sending {source} order email from {engineer_email}")
        
        msg = Message(subject, recipients=recipients, cc=[engineer_email], body=body_text)
        mail.send(msg)
        logger.info(f"SUCCESS: Email sent successfully to {recipients}")

        # Save to database only after successful email send
        if source == "reagents":
            new_order = ReagentOrder(email=engineer_email, date=datetime.utcnow())
            for pnum, item in basket.items():
                new_order.items.append(
                    ReagentOrderItem(part_number=pnum, description=item["description"], quantity=item["quantity"])
                )
        else:
            new_order = PartsOrder(email=engineer_email, date=datetime.utcnow())
            for pnum, item in basket.items():
                new_order.items.append(
                    PartsOrderItem(part_number=pnum, description=item["description"], quantity=item["quantity"])
                )

        db.session.add(new_order)
        db.session.commit()

        session["basket"] = {}
        flash("Your order has been sent!", "success")
        return render_template("confirmation.html")
        
    except Exception as e:
        logger.error(f"ERROR: Failed to send {source} order email - {str(e)}")
        flash(f"Failed to send order. Please contact IT support.", "danger")
        return redirect(url_for("view_reagents_basket" if source == "reagents" else "view_parts_basket"))

# Reorder (reagents)
@app.route("/reorder", methods=["GET", "POST"])
def reorder():
    email = (request.form.get("email_user") or "").strip() if request.method == "POST" else None
    full_email = normalize_order_email(email) if email else None
    orders = []

    if request.method == "POST" and email and not full_email:
        flash("Use your full Servitech email address (example: tom@servitech.co.uk).", "warning")

    if full_email:
        results = (
            ReagentOrder.query
            .filter_by(email=full_email.lower())
            .order_by(ReagentOrder.date.desc())
            .limit(2)
            .all()
        )
        for order in results:
            date_clean = order.date.split(".")[0] if isinstance(order.date, str) else order.date.strftime("%Y-%m-%d %H:%M:%S")
            items_list = [{"part_number": i.part_number, "description": i.description, "quantity": i.quantity} for i in order.items]
            orders.append({"date": date_clean, "items": items_list})

    return render_template("reorder.html", email=full_email, orders=orders)

@app.route("/reorder_submit", methods=["POST"])
def reorder_submit():
    email = request.form.get("email")
    idx = int(request.form.get("order_index", 0))
    orders = (
        ReagentOrder.query
        .filter_by(email=email.lower())
        .order_by(ReagentOrder.date.desc())
        .limit(2)
        .all()
    )
    if idx >= len(orders):
        flash("Invalid reorder index.", "danger")
        return redirect(url_for("reorder"))

    sel = orders[idx]
    lines = [f"{i.part_number} – {i.description} x{i.quantity}" for i in sel.items]
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    body = f"Engineer {email} reorders:\n\n" + "\n".join(lines) + f"\n\n[Reordered on {timestamp}]"
    
    try:
        msg = Message(
            subject=f"***REAGENTS REORDER REQUEST FROM {email}***",
            recipients=["Purchasing@servitech.co.uk"],
            cc=[email],
            body=body
        )
        mail.send(msg)
        logger.info(f"SUCCESS: Reorder email sent to Purchasing for {email}")
        flash("Reorder sent!", "success")
        return render_template("confirmation.html")
    except Exception as e:
        logger.error(f"ERROR: Failed to send reorder email - {str(e)}")
        flash(f"Failed to send reorder. Error: {str(e)}", "danger")
        return redirect(url_for("reorder"))

@app.route("/reorder_to_basket", methods=["POST"])
def reorder_to_basket():
    email = request.form.get("email")
    idx = int(request.form.get("order_index", 0))
    orders = ReagentOrder.query.filter_by(email=email.lower()).order_by(ReagentOrder.date.desc()).limit(2).all()
    if idx >= len(orders):
        flash("Invalid reorder index.", "danger")
        return redirect(url_for("reorder"))

    sel = orders[idx]
    basket = session.get("basket", {})
    for item in sel.items:
        pnum = item.part_number
        if pnum in basket:
            basket[pnum]["quantity"] += item.quantity
        else:
            basket[pnum] = {
                "description": item.description, "quantity": item.quantity,
                "category": "Reagents", "make": "", "manufacturer": "", "image": "", "colour": get_part_colour(pnum)
            }
    session["basket"] = basket
    flash("Added past order to basket!", "success")
    return redirect(url_for("view_reagents_basket"))

# ── Stocktake Routes ─────────────────────────────────────────────────────────

@app.route("/stocktake", methods=["GET", "POST"])
def stocktake_start():
    if request.method == "GET":
        return render_template("stocktake_start.html")

    engineer_user = request.form.get("email_user", "")
    engineer_email = normalize_engineer_email(engineer_user)
    if not engineer_email:
        flash("Use your full Servitech email address (example: tom@servitech.co.uk).", "warning")
        return redirect(url_for("stocktake_start"))

    run = get_or_create_active_stocktake_run()
    existing_stocktake = (
        Stocktake.query
        .join(StocktakeRun, Stocktake.run_id == StocktakeRun.id)
        .filter(
            Stocktake.engineer_email == engineer_email,
            StocktakeRun.name == run.name,
        )
        .order_by(Stocktake.created_at.desc(), Stocktake.id.desc())
        .first()
    )

    if existing_stocktake and existing_stocktake.status in {"submitted", "checked"}:
        flash(
            f"You already submitted {run.name}. Your latest submission is locked.",
            "warning"
        )
        return redirect(url_for("stocktake_review", engineer_email=engineer_email))

    return redirect(url_for("stocktake_page", engineer_email=engineer_email))


@app.route("/stocktake/<engineer_email>", methods=["GET"])
def stocktake_page(engineer_email):
    run = get_or_create_active_stocktake_run()

    # Get or create engineer stocktake for this run
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        st = Stocktake(run_id=run.id, engineer_email=engineer_email.lower(), status="draft")
        db.session.add(st)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()

    submitted = st.status in {"submitted", "checked"}
    submitted_at = st.submitted_at.strftime("%Y-%m-%d %H:%M UTC") if st.submitted_at else None

    # Catalogue filtering mirrors /catalogue but excludes reagents (same as your parts catalogue base) :contentReference[oaicite:5]{index=5}
    base = [p for p in parts_db if "reagent" not in (p.get("category", "").lower())]
    hidden = get_hidden_part_numbers()
    parts = [p for p in base if norm_pn(p.get("part_number", "")) not in hidden]

    category = request.args.get("category")
    search = (request.args.get("search") or "").strip().lower()

    if search:
        filtered = [
            p for p in parts
            if (not category or p.get("category") == category) and (
                search in (p.get("part_number", "").lower()) or
                search in (p.get("description", "").lower()) or
                search in (p.get("make", "").lower()) or
                search in (p.get("manufacturer", "").lower())
            )
        ]
    else:
        filtered = [p for p in parts if not category or p.get("category") == category]

    items = (
        StocktakeItem.query
        .filter_by(stocktake_id=st.id)
        .order_by(StocktakeItem.part_number.asc())
        .all()
    )
    stocktake_rows = get_stocktake_rows_with_unfound(st.id)

    # remember last engineer on this device/browser
    session["last_stocktake_email"] = engineer_email.lower()

    # map of qtys for fast UI display in catalogue list
    item_qty_map = {it.part_number: int(it.quantity or 0) for it in items}

    # counts for badge / remote count display
    mine_lines, mine_total_qty = stocktake_counts(st.id)

    return render_template(
        "stocktake_page.html",
        engineer_email=engineer_email.lower(),
        run_name=run.name,
        submitted=submitted,
        submitted_at=submitted_at,
        parts=filtered,
        categories=get_categories(parts),
        selected_category=category,
        search=search,
        items=items,
        stocktake_rows=stocktake_rows,
        item_qty_map=item_qty_map,
        mine_lines=mine_lines,
        mine_total_qty=mine_total_qty,
    )


@app.route("/stocktake/<engineer_email>/add-unfound", methods=["POST"])
def stocktake_add_unfound(engineer_email):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        flash("Stocktake not found. Start again.", "warning")
        return redirect(url_for("stocktake_start"))

    if st.status in {"submitted", "checked"}:
        flash("This stocktake is already submitted and locked.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    part_code = (request.form.get("part_code") or "").strip()
    description = (request.form.get("description") or "").strip()
    try:
        quantity = int(request.form.get("quantity", 0))
    except ValueError:
        quantity = 0

    if not part_code or not description or quantity <= 0:
        flash("For Part unfound, enter code, description, and a quantity above zero.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    db.session.add(StocktakeUnfoundItem(
        stocktake_id=st.id,
        part_code=part_code,
        description=description,
        quantity=quantity,
    ))
    db.session.commit()
    flash("Part unfound line added.", "success")
    return redirect(url_for("stocktake_page", engineer_email=engineer_email, view="mine"))


@app.route("/stocktake/<engineer_email>/add/<path:part_number>")
def stocktake_add_item(engineer_email, part_number):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        flash("Stocktake not found. Start again.", "warning")
        return redirect(url_for("stocktake_start"))

    if st.status in {"submitted", "checked"}:
        flash("This stocktake is already submitted and locked.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    part = next((p for p in parts_db if p["part_number"] == part_number), None)
    if not part:
        flash("Part not found.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()
    if item:
        item.quantity += 1
    else:
        item = StocktakeItem(
            stocktake_id=st.id,
            part_number=part_number,
            description=part.get("description", ""),
            quantity=1
        )
        db.session.add(item)

    db.session.commit()
    return redirect(request.referrer or url_for("stocktake_page", engineer_email=engineer_email))


@app.route("/stocktake/<engineer_email>/update/<path:part_number>", methods=["POST"])
def stocktake_update_item(engineer_email, part_number):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Stocktake not found."}), 404
        flash("Stocktake not found.", "warning")
        return redirect(url_for("stocktake_start"))

    if st.status in {"submitted", "checked"}:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "This stocktake is locked."}), 403
        flash("This stocktake is already submitted and locked.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    try:
        qty = int(request.form.get("quantity", 0))
    except ValueError:
        qty = 0

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()
    if not item:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Item not found."}), 404
        flash("Item not found.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    removed = False
    if qty <= 0:
        db.session.delete(item)
        removed = True
    else:
        item.quantity = qty

    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        items_count = StocktakeItem.query.filter_by(stocktake_id=st.id).count()
        return jsonify({
            "ok": True,
            "removed": removed,
            "part_number": part_number,
            "quantity": None if removed else item.quantity,
            "items_count": items_count
        })

    return redirect(url_for("stocktake_page", engineer_email=engineer_email))


@app.route("/stocktake/<engineer_email>/remove/<path:part_number>")
def stocktake_remove_item(engineer_email, part_number):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        flash("Stocktake not found.", "warning")
        return redirect(url_for("stocktake_start"))

    if st.status in {"submitted", "checked"}:
        flash("This stocktake is already submitted and locked.", "warning")
        return redirect(url_for("stocktake_page", engineer_email=engineer_email))

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()
    if item:
        db.session.delete(item)
        db.session.commit()

    return redirect(url_for("stocktake_page", engineer_email=engineer_email))


@app.route("/stocktake/<engineer_email>/set/<path:part_number>", methods=["POST"])
def stocktake_set_item_qty(engineer_email, part_number):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()

    if not st:
        return jsonify({"ok": False, "error": "Stocktake not found."}), 404

    if st.status in {"submitted", "checked"}:
        return jsonify({"ok": False, "error": "This stocktake is submitted and locked."}), 400

    try:
        qty = int(request.form.get("quantity", 0))
    except ValueError:
        qty = 0
    qty = max(0, qty)

    part = next((p for p in parts_db if p["part_number"] == part_number), None)
    if not part:
        return jsonify({"ok": False, "error": "Part not found."}), 404

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()

    removed = False
    if qty <= 0:
        if item:
            db.session.delete(item)
            removed = True
    else:
        if item:
            item.quantity = qty
        else:
            item = StocktakeItem(
                stocktake_id=st.id,
                part_number=part_number,
                description=part.get("description", ""),
                quantity=qty
            )
            db.session.add(item)

    db.session.commit()

    items_count, total_qty = stocktake_counts(st.id)

    return jsonify({
        "ok": True,
        "part_number": part_number,
        "quantity": 0 if removed else qty,
        "removed": removed,
        "items_count": items_count,
        "total_qty": total_qty,
    })


@app.route("/stocktake/<engineer_email>/counts")
def stocktake_counts_api(engineer_email):
    """Small polling endpoint so the engineer UI can show a live 'items in box' count."""
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        return jsonify({"ok": True, "items_count": 0, "total_qty": 0})
    items_count, total_qty = stocktake_counts(st.id)
    return jsonify({"ok": True, "items_count": items_count, "total_qty": total_qty})


@app.route("/stocktake/<engineer_email>/review", methods=["GET"])
def stocktake_review(engineer_email):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        flash("Stocktake not found.", "warning")
        return redirect(url_for("stocktake_start"))

    stocktake_rows = get_stocktake_rows_with_unfound(st.id)
    total_qty = sum([int(i.get("quantity") or 0) for i in stocktake_rows])

    submitted = st.status in {"submitted", "checked"}
    submitted_at = st.submitted_at.strftime("%Y-%m-%d %H:%M UTC") if st.submitted_at else None

    return render_template(
        "stocktake_review.html",
        engineer_email=engineer_email.lower(),
        run_name=run.name,
        stocktake_rows=stocktake_rows,
        total_qty=total_qty,
        submitted=submitted,
        submitted_at=submitted_at
    )


@app.route("/stocktake/<engineer_email>/submit", methods=["POST"])
def stocktake_submit(engineer_email):
    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.filter_by(run_id=run.id, engineer_email=engineer_email.lower()).first()
    if not st:
        flash("Stocktake not found.", "warning")
        return redirect(url_for("stocktake_start"))

    if st.status in {"submitted", "checked"}:
        flash("Already submitted.", "success")
        return redirect(url_for("stocktake_review", engineer_email=engineer_email))

    ack = request.form.get("ack")
    confirm_text = (request.form.get("confirm_text") or "").strip().upper()
    if ack != "yes" or confirm_text != "SUBMIT":
        flash("To submit, tick the checkbox and type SUBMIT.", "warning")
        return redirect(url_for("stocktake_review", engineer_email=engineer_email))

    stocktake_rows = get_stocktake_rows_with_unfound(st.id)
    if not stocktake_rows:
        flash("You can’t submit an empty stocktake.", "warning")
        return redirect(url_for("stocktake_review", engineer_email=engineer_email))

    # Lock it first (so refresh/double-click can’t double-submit)
    st.status = "submitted"
    st.submitted_at = datetime.utcnow()
    # If it was previously checked, clear check-off so it can be re-checked
    st.checked_by = None
    st.checked_at = None
    db.session.commit()

    # Build email body
    lines = []
    for row in stocktake_rows:
        line = (
            f"• Part Number: {row.get('part_number','')}\n"
            f"  Description: {row.get('description','')}\n"
            f"  Quantity: {row.get('quantity', 0)}"
        )
        if row.get("is_unfound"):
            line += "\n  Type: Part unfound"
        else:
            line += f"\n  Colour: {row.get('colour') or 'Not set'}"
        lines.append(line)

    body_text = (
        f"Engineer: {engineer_email.lower()}\n"
        f"Run: {run.name}\n"
        f"Submitted: {st.submitted_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "Stocktake Items:\n\n" + "\n\n".join(lines) + "\n"
    )

    leader = stocktake_leader_email()
    subject = f"STOCKTAKE SUBMITTED - {engineer_email.lower()} - {run.name}"

    try:
        msg_engineer = Message(
            subject=f"STOCKTAKE CONFIRMATION - {run.name}",
            recipients=[engineer_email.lower()],
            body=body_text
        )
        mail.send(msg_engineer)

        msg_leader = Message(
            subject=subject,
            recipients=[leader],
            cc=[engineer_email.lower()],
            body=body_text
        )
        mail.send(msg_leader)

        logger.info(f"SUCCESS: Stocktake submitted by {engineer_email.lower()} and emailed to {leader}")

    except Exception as e:
        # Stocktake remains submitted even if email fails (better than letting duplicates happen)
        logger.error(f"ERROR: Stocktake submitted but email failed - {str(e)}")

    return render_template(
        "stocktake_confirmation.html",
        engineer_email=engineer_email.lower(),
        run_name=run.name,
        submitted_at=st.submitted_at.strftime("%Y-%m-%d %H:%M UTC")
    )


@app.route("/stocktake/parts_search")
def stocktake_parts_search():
    """
    Live search endpoint used by the stocktake page.
    Returns a filtered list of parts from parts_db.
    Logic matches the normal stocktake catalogue filtering.
    """
    q = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("category") or "").strip()

    # Match your existing logic (exclude reagents, remove hidden parts)
    base = [p for p in parts_db if "reagent" not in (p.get("category", "").lower())]
    hidden = get_hidden_part_numbers()

    results = []
    for p in base:
        pn = p.get("part_number", "") or ""
        if not pn:
            continue
        if norm_pn(pn) in hidden:
            continue
        if category and (p.get("category") or "") != category:
            continue

        if q:
            hay = f"{pn} {p.get('description','')} {p.get('make','')} {p.get('manufacturer','')}".lower()
            if q not in hay:
                continue

        results.append({
            "part_number": pn,
            "description": p.get("description", "") or "",
            "colour": p.get("colour", "") or "",
        })

        # Safety limit so you don't return thousands every keystroke
        if len(results) >= 250:
            break

    return jsonify({"ok": True, "parts": results})


def parts_admin_password() -> str:
    return (os.environ.get("PARTS_ADMIN_PASSWORD") or "dan123").strip()


def require_parts_admin():
    if session.get("parts_admin_authed"):
        return None
    return redirect(url_for("parts_admin"))


@app.route("/parts-admin", methods=["GET", "POST"])
def parts_admin():
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()

        if action == "login":
            password = (request.form.get("password") or "").strip()
            if password != parts_admin_password():
                flash("Incorrect parts admin password.", "warning")
                return redirect(url_for("parts_admin"))
            session["parts_admin_authed"] = True
            flash("Parts admin access granted.", "success")
            return redirect(url_for("parts_admin"))

        guard = require_parts_admin()
        if guard:
            return guard

        if action == "logout":
            session.pop("parts_admin_authed", None)
            flash("Signed out.", "success")
            return redirect(url_for("parts_admin"))

        working = [dict(p) for p in parts_db]

        if action == "update":
            original_part_number = (request.form.get("original_part_number") or "").strip()
            updated_part_number = (request.form.get("part_number") or "").strip()
            if not original_part_number or not updated_part_number:
                flash("Part code is required.", "warning")
                return redirect(url_for("parts_admin"))

            if original_part_number != updated_part_number:
                duplicate = next((p for p in working if p["part_number"] == updated_part_number), None)
                if duplicate:
                    flash("Cannot change part code because the new code already exists.", "warning")
                    return redirect(url_for("parts_admin"))

            target = next((p for p in working if p["part_number"] == original_part_number), None)
            if not target:
                flash("Part to update was not found.", "warning")
                return redirect(url_for("parts_admin"))

            target["part_number"] = updated_part_number
            target["description"] = (request.form.get("description") or "").strip()
            target["category"] = (request.form.get("category") or "").strip()
            target["make"] = (request.form.get("make") or "").strip()
            target["manufacturer"] = (request.form.get("manufacturer") or "").strip()
            target["image"] = (request.form.get("image") or "").strip()
            target["colour"] = normalize_colour(request.form.get("colour", ""))
            target["installs"] = request.form.get("installs") == "on"

            save_parts_catalogue(working)
            refresh_parts_catalogue()
            flash(f"Updated {updated_part_number}.", "success")
            return redirect(url_for("parts_admin"))

        if action == "add":
            new_part_number = (request.form.get("part_number") or "").strip()
            if not new_part_number:
                flash("Part code is required for new part.", "warning")
                return redirect(url_for("parts_admin"))

            duplicate = next((p for p in working if p["part_number"] == new_part_number), None)
            if duplicate:
                flash("That part code already exists.", "warning")
                return redirect(url_for("parts_admin"))

            working.append({
                "part_number": new_part_number,
                "description": (request.form.get("description") or "").strip(),
                "category": (request.form.get("category") or "").strip(),
                "make": (request.form.get("make") or "").strip(),
                "manufacturer": (request.form.get("manufacturer") or "").strip(),
                "image": (request.form.get("image") or "").strip(),
                "colour": normalize_colour(request.form.get("colour", "")),
                "installs": request.form.get("installs") == "on",
            })

            save_parts_catalogue(working)
            refresh_parts_catalogue()
            flash(f"Added {new_part_number}.", "success")
            return redirect(url_for("parts_admin"))

    if not session.get("parts_admin_authed"):
        return render_template("parts_admin.html", authed=False)

    search = (request.args.get("search") or "").strip().lower()
    if search:
        visible_parts = [
            p for p in parts_db
            if (
                search in (p.get("part_number", "").lower())
                or search in (p.get("description", "").lower())
                or search in (p.get("category", "").lower())
            )
        ]
    else:
        visible_parts = list(parts_db)

    visible_parts.sort(key=lambda x: (x.get("part_number") or "").lower())

    return render_template(
        "parts_admin.html",
        authed=True,
        parts=visible_parts,
        search=search,
        colours=ALLOWED_COLOURS,
    )


@app.route("/stocktake-leader", methods=["GET", "POST"])
def stocktake_leader_login():
    if request.method == "GET":
        return render_template("stocktake_leader_login.html")

    pw = (request.form.get("password") or "").strip()
    if not stocktake_leader_password():
        flash("Leader password not set. Add STOCKTAKE_LEADER_PASSWORD in environment variables.", "warning")
        return redirect(url_for("stocktake_leader_login"))

    if pw != stocktake_leader_password():
        flash("Incorrect password.", "warning")
        return redirect(url_for("stocktake_leader_login"))

    session["stocktake_leader_authed"] = True
    return redirect(url_for("stocktake_leader_dashboard"))
def require_stocktake_leader():
    """Guard helper used by leader routes.

    Returns None when the session is authorised, otherwise returns a redirect
    response to the leader login page. This matches existing callsites that
    do `guard = require_stocktake_leader(); if guard: return guard`.
    """
    if session.get("stocktake_leader_authed"):
        return None
    return redirect(url_for("stocktake_leader_login"))


@app.route("/stocktake-leader/logout")
def stocktake_leader_logout():
    session.pop("stocktake_leader_authed", None)
    return redirect(url_for("stocktake_leader_login"))


@app.route("/stocktake-leader/dashboard")
def stocktake_leader_dashboard():
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()

    # Get ALL stocktakes for this run (draft + submitted)
    all_stocktakes = (
        Stocktake.query
        .filter_by(run_id=run.id)
        .all()
    )

    submissions = []
    for st in all_stocktakes:
        items = StocktakeItem.query.filter_by(stocktake_id=st.id).all()
        unfound_items = StocktakeUnfoundItem.query.filter_by(stocktake_id=st.id).all()

        # IMPORTANT: use submitted_at (not submitted_at_str) so templates can detect it
        submitted_at = (
            st.submitted_at.strftime("%Y-%m-%d %H:%M UTC")
            if st.submitted_at else None
        )

        submissions.append({
            "id": st.id,
            "engineer_email": st.engineer_email,
            "submitted_at": submitted_at,     # <-- template-friendly
            "lines": len(items) + len(unfound_items),
            "total_qty": sum(int(i.quantity or 0) for i in items) + sum(int(i.quantity or 0) for i in unfound_items),
            "status": st.status,
            "checked_by": st.checked_by,
            "checked_at": st.checked_at.strftime("%Y-%m-%d %H:%M UTC") if st.checked_at else None,
        })

    # Sort: submitted first, then pending, newest submitted first
    def sort_key(x):
        is_pending = (x.get("submitted_at") is None)
        # pending goes after submitted, submitted sorted by time desc (string is fine here)
        return (is_pending, "" if x.get("submitted_at") is None else x.get("submitted_at"))

    submissions.sort(key=sort_key, reverse=True)

    return render_template(
        "stocktake_leader_dashboard.html",
        run_name=run.name,
        submissions=submissions
    )


@app.route("/stocktake-leader/update-run-name", methods=["POST"])
def stocktake_leader_update_run_name():
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()
    new_name = (request.form.get("run_name") or "").strip()
    
    if not new_name:
        flash("Run name cannot be empty.", "warning")
        return redirect(url_for("stocktake_leader_dashboard"))
    
    run.name = new_name
    db.session.commit()
    flash(f"Stock take name updated to '{new_name}'.", "success")
    return redirect(url_for("stocktake_leader_dashboard"))


@app.route("/stocktake-leader/engineer/<int:stocktake_id>")
def stocktake_leader_view_engineer(stocktake_id):
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.get_or_404(stocktake_id)

    if st.run_id != run.id:
        flash("That stocktake is not part of the active run.", "warning")
        return redirect(url_for("stocktake_leader_dashboard"))

    stocktake_rows = get_stocktake_rows_with_unfound(st.id)

    return render_template(
        "stocktake_leader_engineer.html",
        stocktake_id=st.id,
        engineer_email=st.engineer_email,
        run_name=run.name,
        submitted_at=st.submitted_at.strftime("%Y-%m-%d %H:%M UTC") if st.submitted_at else "Not submitted yet",
        stocktake_rows=stocktake_rows,
    )


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/edit")
def stocktake_leader_edit_engineer(stocktake_id):
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()
    st = Stocktake.query.get_or_404(stocktake_id)

    # Optional: keep edits on active run only
    if st.run_id != run.id:
        flash("That stocktake is not part of the active run.", "warning")
        return redirect(url_for("stocktake_leader_dashboard"))

    # Catalogue filtering (same logic as stocktake_page)
    base = [p for p in parts_db if "reagent" not in (p.get("category", "").lower())]
    hidden = get_hidden_part_numbers()
    parts = [p for p in base if norm_pn(p.get("part_number", "")) not in hidden]

    category = request.args.get("category")
    search = (request.args.get("search") or "").strip().lower()

    if search:
        filtered = [
            p for p in parts
            if (not category or p.get("category") == category) and (
                search in (p.get("part_number", "").lower()) or
                search in (p.get("description", "").lower()) or
                search in (p.get("make", "").lower()) or
                search in (p.get("manufacturer", "").lower())
            )
        ]
    else:
        filtered = [p for p in parts if not category or p.get("category") == category]

    items = StocktakeItem.query.filter_by(stocktake_id=st.id).order_by(StocktakeItem.part_number.asc()).all()
    stocktake_rows = get_stocktake_rows_with_unfound(st.id)

    item_qty_map = {it.part_number: int(it.quantity or 0) for it in items}

    return render_template(
        "stocktake_leader_engineer_edit.html",
        stocktake_id=st.id,
        engineer_email=st.engineer_email,
        run_name=run.name,
        submitted_at=st.submitted_at.strftime("%Y-%m-%d %H:%M UTC") if st.submitted_at else "—",
        status=st.status,
        checked_by=st.checked_by,
        checked_at=st.checked_at.strftime("%Y-%m-%d %H:%M UTC") if st.checked_at else None,
        items=items,
        stocktake_rows=stocktake_rows,
        item_qty_map=item_qty_map,
        parts=filtered,
        categories=get_categories(parts),
        selected_category=category,
        search=search
    )


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/add/<path:part_number>")
def stocktake_leader_add_item(stocktake_id, part_number):
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)

    part = next((p for p in parts_db if p["part_number"] == part_number), None)
    if not part:
        flash("Part not found.", "warning")
        return redirect(url_for("stocktake_leader_edit_engineer", stocktake_id=stocktake_id))

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()
    if item:
        item.quantity += 1
    else:
        db.session.add(StocktakeItem(
            stocktake_id=st.id,
            part_number=part_number,
            description=part.get("description", ""),
            quantity=1
        ))

    db.session.commit()
    return redirect(request.referrer or url_for("stocktake_leader_edit_engineer", stocktake_id=stocktake_id))


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/remove/<path:part_number>")
def stocktake_leader_remove_item(stocktake_id, part_number):
    guard = require_stocktake_leader()
    if guard:
        return guard

    item = StocktakeItem.query.filter_by(stocktake_id=stocktake_id, part_number=part_number).first()
    if item:
        db.session.delete(item)
        db.session.commit()

    return redirect(url_for("stocktake_leader_edit_engineer", stocktake_id=stocktake_id))


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/update/<path:part_number>", methods=["POST"])
def stocktake_leader_update_item(stocktake_id, part_number):
    guard = require_stocktake_leader()
    if guard:
        return guard

    item = StocktakeItem.query.filter_by(stocktake_id=stocktake_id, part_number=part_number).first()
    if not item:
        return jsonify({"ok": False, "error": "Item not found."}), 404

    try:
        qty = int(request.form.get("quantity", 0))
    except ValueError:
        qty = 0

    removed = False
    if qty <= 0:
        db.session.delete(item)
        removed = True
    else:
        item.quantity = qty

    db.session.commit()

    items_count = StocktakeItem.query.filter_by(stocktake_id=stocktake_id).count()
    return jsonify({
        "ok": True,
        "removed": removed,
        "quantity": None if removed else qty,
        "items_count": items_count
    })


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/set/<path:part_number>", methods=["POST"])
def stocktake_leader_set_item_qty(stocktake_id, part_number):
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)

    try:
        qty = int(request.form.get("quantity", 0))
    except ValueError:
        qty = 0
    qty = max(0, qty)

    part = next((p for p in parts_db if p["part_number"] == part_number), None)
    if not part:
        return jsonify({"ok": False, "error": "Part not found."}), 404

    item = StocktakeItem.query.filter_by(stocktake_id=st.id, part_number=part_number).first()

    removed = False
    if qty <= 0:
        if item:
            db.session.delete(item)
            removed = True
    else:
        if item:
            item.quantity = qty
        else:
            db.session.add(StocktakeItem(
                stocktake_id=st.id,
                part_number=part_number,
                description=part.get("description", ""),
                quantity=qty
            ))

    db.session.commit()

    items_count = StocktakeItem.query.filter_by(stocktake_id=st.id).count()
    return jsonify({
        "ok": True,
        "removed": removed,
        "quantity": 0 if removed else qty,
        "items_count": items_count
    })


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/unlock", methods=["POST"])
def stocktake_leader_unlock(stocktake_id):
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)

    # unlock (draft again)
    st.status = "draft"
    st.submitted_at = None
    st.checked_by = None
    st.checked_at = None

    db.session.commit()
    flash(f"Unlocked {st.engineer_email} stocktake.", "success")
    return redirect(url_for("stocktake_leader_dashboard"))


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/delete", methods=["POST"])
def stocktake_leader_delete(stocktake_id):
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)

    # Only allow delete if status is submitted or checked, not if pending (draft)
    if st.status == "draft":
        flash("Cannot delete a pending stocktake. It must be submitted first.", "warning")
        return redirect(url_for("stocktake_leader_dashboard"))

    engineer_email = st.engineer_email
    
    # Delete all items for that stocktake (cascade handles this, but be explicit)
    StocktakeItem.query.filter_by(stocktake_id=st.id).delete(synchronize_session=False)
    StocktakeUnfoundItem.query.filter_by(stocktake_id=st.id).delete(synchronize_session=False)
    
    # Delete the stocktake itself
    db.session.delete(st)
    db.session.commit()
    
    flash(f"Deleted stocktake for {engineer_email}.", "success")
    return redirect(url_for("stocktake_leader_dashboard"))


@app.route("/stocktake-leader/engineer/<int:stocktake_id>/check", methods=["POST"])
def stocktake_leader_check(stocktake_id):
    """Mark a submitted stocktake as CHECKED and store who checked it + timestamp."""
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)

    checker = (request.form.get("checked_by") or "").strip()
    if not checker:
        flash("Please enter a name before marking as checked.", "warning")
        return redirect(url_for("stocktake_leader_edit_engineer", stocktake_id=stocktake_id))

    # You can choose to enforce 'submitted first' by uncommenting below.
    # if st.status != "submitted":
    #     flash("Only submitted stocktakes can be checked.", "warning")
    #     return redirect(url_for("stocktake_leader_edit_engineer", stocktake_id=stocktake_id))

    st.status = "checked"
    st.checked_by = checker
    st.checked_at = datetime.utcnow()

    db.session.commit()
    flash(f"Marked as checked by {checker}.", "success")
    return redirect(url_for("stocktake_leader_dashboard"))


# CSV export helper and routes for stocktake leader
def csv_response(filename: str, rows: list, header: list):
    sio = StringIO()
    writer = csv.writer(sio)
    writer.writerow(header)
    writer.writerows(rows)
    output = make_response(sio.getvalue())
    output.headers["Content-Type"] = "text/csv"
    output.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return output


def build_master_totals_for_run(run_id: int):
    """
    Returns list of dicts: [{part_number, description, total_qty}]
    Totals are computed from DB (single source of truth).
    Only includes submitted stocktakes for the given run.
    """
    # IMPORTANT: cast quantity -> Integer so SUM is correct even if stored as text
    rows = (
        db.session.query(
            StocktakeItem.part_number.label("part_number"),
            func.sum(cast(StocktakeItem.quantity, Integer)).label("total_qty"),
        )
        .join(Stocktake, StocktakeItem.stocktake_id == Stocktake.id)
        .filter(
            Stocktake.run_id == run_id,
            Stocktake.status == "submitted",
        )
        .group_by(StocktakeItem.part_number)
        .order_by(StocktakeItem.part_number.asc())
        .all()
    )

    # Optional: description lookup (works if you have parts_db list/dict in memory)
    # If you don't have parts_db, you can remove the description column.
    desc_map = {}
    try:
        # parts_db: list of dicts (part_number, description)
        desc_map = { (p.get("part_number") or "").strip(): (p.get("description") or "") for p in parts_db }
    except Exception:
        desc_map = {}

    out = []
    for pn, total_qty in rows:
        pn_clean = (pn or "").strip()
        out.append({
            "part_number": pn_clean,
            "description": desc_map.get(pn_clean, ""),
            "total_qty": int(total_qty or 0),
        })

    unfound_rows = (
        db.session.query(
            StocktakeUnfoundItem.part_code.label("part_number"),
            StocktakeUnfoundItem.description.label("description"),
            func.sum(cast(StocktakeUnfoundItem.quantity, Integer)).label("total_qty")
        )
        .join(Stocktake, StocktakeUnfoundItem.stocktake_id == Stocktake.id)
        .filter(
            Stocktake.run_id == run_id,
            Stocktake.status == "submitted",
        )
        .group_by(StocktakeUnfoundItem.part_code, StocktakeUnfoundItem.description)
        .all()
    )
    for uf in unfound_rows:
        out.append({
            "part_number": uf.part_number,
            "description": f"[UNFOUND] {uf.description}",
            "total_qty": int(uf.total_qty or 0),
        })

    out.sort(key=lambda x: (str(x.get("part_number") or "").lower(), str(x.get("description") or "").lower()))
    return out


@app.route("/stocktake-leader/export/master.csv")
def stocktake_leader_export_master():
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()
    master = build_master_totals_for_run(run.id)

    # Build CSV
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["Part Number", "Description", "Total Qty"])
    for row in master:
        w.writerow([row["part_number"], row["description"], row["total_qty"]])

    filename = f"stocktake_master_run_{run.id}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/stocktake-leader/export/all.csv")
def stocktake_leader_export_all():
    guard = require_stocktake_leader()
    if guard:
        return guard

    run = get_or_create_active_stocktake_run()

    # Pull all submitted stocktakes for this run
    submissions = (
        Stocktake.query
        .filter_by(run_id=run.id, status="submitted")
        .order_by(Stocktake.submitted_at.asc())
        .all()
    )

    # Optional: description lookup from parts_db (safe fallback)
    desc_map = {}
    try:
        desc_map = {(p.get("part_number") or "").strip(): (p.get("description") or "") for p in parts_db}
    except Exception:
        desc_map = {}

    buf = StringIO()
    w = csv.writer(buf)

    # One file, all engineers, one row per item
    w.writerow(["Engineer Email", "Submitted At", "Part Number", "Description", "Quantity", "Run"])

    for sub in submissions:
        engineer = getattr(sub, "engineer_email", None) or ""
        submitted_at = getattr(sub, "submitted_at", None)
        submitted_at_str = submitted_at.strftime("%Y-%m-%d %H:%M:%S") if submitted_at else ""

        items = (
            StocktakeItem.query
            .filter_by(stocktake_id=sub.id)
            .order_by(StocktakeItem.part_number.asc())
            .all()
        )

        for it in items:
            pn = (it.part_number or "").strip()
            qty = int(it.quantity or 0)
            w.writerow([engineer, submitted_at_str, pn, desc_map.get(pn, ""), qty, run.name])

        unfound_items = (
            StocktakeUnfoundItem.query
            .filter_by(stocktake_id=sub.id)
            .order_by(StocktakeUnfoundItem.part_code.asc(), StocktakeUnfoundItem.id.asc())
            .all()
        )
        for uf in unfound_items:
            w.writerow([engineer, submitted_at_str, uf.part_code, f"[UNFOUND] {uf.description}", int(uf.quantity or 0), run.name])

    filename = f"stocktake_all_{run.name.replace(' ', '_')}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/stocktake-leader/export/engineer/<int:stocktake_id>.csv")
def stocktake_leader_export_engineer(stocktake_id):
    guard = require_stocktake_leader()
    if guard:
        return guard

    st = Stocktake.query.get_or_404(stocktake_id)
    items = StocktakeItem.query.filter_by(stocktake_id=st.id).order_by(StocktakeItem.part_number.asc()).all()

    rows = [(st.engineer_email, it.part_number, it.description, it.quantity) for it in items]

    unfound_items = (
        StocktakeUnfoundItem.query
        .filter_by(stocktake_id=st.id)
        .order_by(StocktakeUnfoundItem.part_code.asc(), StocktakeUnfoundItem.id.asc())
        .all()
    )
    rows.extend([(st.engineer_email, uf.part_code, f"[UNFOUND] {uf.description}", uf.quantity) for uf in unfound_items])
    return csv_response(
        f"stocktake_{st.engineer_email}.csv",
        rows,
        ["engineer_email", "part_number", "description", "quantity"]
    )



# ── My Orders (three-section view) ────────────────────────────────────────────
@app.route("/my-orders", methods=["GET"])
def my_orders():
    """
    Engineer-facing order view:
    - Back Orders: items explicitly marked back_order=True and still remaining > 0
    - Active Orders: other outstanding items (remaining > 0) not flagged as back order
    - Sent Items (Last 7 Days / Over 7 Days): from dispatch notes/items
    - last_dispatch_for_part: map for the "Last Dispatched" column
    """
    email = (request.args.get("email") or "").strip()
    if not email:
        # First load or missing email: show the form only
        return render_template("my_orders.html", email=None)

    # -------- outstanding items (remaining > 0) --------
    # Pull ALL outstanding lines for this engineer (regardless of back_order flag)
    outstanding_items = (
        db.session.query(PartsOrderItem)
        .join(PartsOrder, PartsOrder.id == PartsOrderItem.order_id)
        .filter(
            PartsOrder.email == email,
            (PartsOrderItem.quantity - func.coalesce(PartsOrderItem.quantity_sent, 0)) > 0
        )
        .order_by(PartsOrder.date.asc(), PartsOrderItem.id.asc())
        .all()
    )

    # Split: explicit back orders vs other active items
    back_orders = [i for i in outstanding_items if bool(getattr(i, "back_order", False))]
    active_orders = [i for i in outstanding_items if not bool(getattr(i, "back_order", False))]

    # -------- dispatch history (recent vs older) --------
    now = datetime.utcnow()
    last_7 = now - timedelta(days=7)

    # Recent (≤ 7 days)
    recent_dispatches = (
        db.session.query(
            DispatchNote.date.label("dispatch_date"),
            DispatchItem.part_number.label("part_number"),
            DispatchItem.description.label("description"),
            DispatchItem.quantity_sent.label("qty_sent"),
        )
        .join(DispatchItem, DispatchItem.dispatch_note_id == DispatchNote.id)
        .filter(
            DispatchNote.engineer_email == email,
            DispatchNote.date >= last_7,
        )
        .order_by(DispatchNote.date.desc(), DispatchItem.part_number.asc())
        .all()
    )

    # Older (> 7 days)
    older_dispatches = (
        db.session.query(
            DispatchNote.date.label("dispatch_date"),
            DispatchItem.part_number.label("part_number"),
            DispatchItem.description.label("description"),
            DispatchItem.quantity_sent.label("qty_sent"),
        )
        .join(DispatchItem, DispatchItem.dispatch_note_id == DispatchNote.id)
        .filter(
            DispatchNote.engineer_email == email,
            DispatchNote.date < last_7,
        )
        .order_by(DispatchNote.date.desc(), DispatchItem.part_number.asc())
        .all()
    )

    # -------- last dispatch per part (for the table badges) --------
    last_dispatch_results = (
        db.session.query(
            DispatchItem.part_number.label("part_number"),
            func.max(DispatchNote.date).label("last_date"),
        )
        .join(DispatchNote, DispatchNote.id == DispatchItem.dispatch_note_id)
        .filter(DispatchNote.engineer_email == email)
        .group_by(DispatchItem.part_number)
        .all()
    )
    last_dispatch_for_part = {row.part_number: row.last_date for row in last_dispatch_results}

    colour_for_part = {p.get("part_number", ""): p.get("colour", "") for p in parts_db}

    # Render
    return render_template(
        "my_orders.html",
        email=email,
        back_orders=back_orders,
        active_orders=active_orders,
        recent_dispatches=recent_dispatches,
        older_dispatches=older_dispatches,
        last_dispatch_for_part=last_dispatch_for_part,
        colour_for_part=colour_for_part,
    )

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)

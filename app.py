# ──────────────────────────────────────────────────────────────────────────────
# Servitech Parts ORDER SYSTEM 
# ──────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta
import os
import csv

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from sqlalchemy import desc, func
from typing import Set  

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


# ── CSV catalogue load ────────────────────────────────────────────────────────
parts_db = []
with open("parts.csv", newline="", encoding="cp1252") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        part_number = row["Product Code"].strip().replace("\u00a0", "").replace("\r", "").replace("\n", "")
        clean_filename = part_number.replace("/", "") + ".png"
        parts_db.append({
            "part_number": part_number,
            "description": row["Description"].strip(),
            "category": row["Category"].strip(),
            "make": row.get("Make", "").strip(),
            "manufacturer": row.get("Manufacturer", "").strip(),
            "image": clean_filename,
        })

def get_categories(parts):
    return sorted({p["category"] for p in parts})

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
    # 1) Base list: non-reagents from parts_db
    base = [p for p in parts_db if "reagent" not in (p.get("category", "").lower())]

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
    return render_template("parts_basket.html", basket=session.get("basket", {}))

@app.route("/reagents_basket")
def view_reagents_basket():
    return render_template("reagents_basket.html", basket=session.get("basket", {}))

# Basket actions
@app.route("/add_to_basket/<path:part_number>")
def add_to_basket(part_number):
    part = next((p for p in parts_db if p["part_number"] == part_number), None)
    if not part:
        return redirect(url_for("index"))

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
    engineer_email = f"{engineer_name}@servitech.co.uk".lower()
    source = request.form.get("source", "catalogue")
    basket = session.get("basket", {})

    if not basket or not engineer_email:
        flash("Your basket is empty or email missing", "warning")
        return redirect(url_for("view_reagents_basket" if source == "reagents" else "view_parts_basket"))

    # email body
    lines = [
        f"• Part Number: {pnum}\n  Description: {item['description']}\n  Quantity: {item['quantity']}"
        for pnum, item in basket.items()
    ]
    comments = request.form.get("comments", "").strip()
    if comments:
        lines.append("\nComments:\n" + comments)

    body_text = (
        f"Engineer: {engineer_email}\n"
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
    email = request.form.get("email_user") if request.method == "POST" else None
    full_email = f"{email}@servitech.co.uk" if email else None
    orders = []

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
                "category": "Reagents", "make": "", "manufacturer": "", "image": ""
            }
    session["basket"] = basket
    flash("Added past order to basket!", "success")
    return redirect(url_for("view_reagents_basket"))

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

    # Render
    return render_template(
        "my_orders.html",
        email=email,
        back_orders=back_orders,
        active_orders=active_orders,
        recent_dispatches=recent_dispatches,
        older_dispatches=older_dispatches,
        last_dispatch_for_part=last_dispatch_for_part,
    )

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)

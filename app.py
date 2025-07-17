from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from datetime import datetime
import os
import csv

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-dev-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

### --- Models --- ###
class ReagentOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    items = db.relationship("ReagentOrderItem", backref="order", cascade="all, delete-orphan")

class ReagentOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('reagent_order.id'), nullable=False)
    part_number = db.Column(db.String(64))
    description = db.Column(db.String(256))
    quantity = db.Column(db.Integer)


class PartsOrder(db.Model):
    __tablename__ = 'parts_order'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), nullable=True)
    items = db.relationship("PartsOrderItem", backref="order", cascade="all, delete-orphan")

class PartsOrderItem(db.Model):
    __tablename__ = 'parts_order_item'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('parts_order.id'), nullable=False)
    part_number = db.Column(db.String(64))
    description = db.Column(db.String(256))
    quantity = db.Column(db.Integer)
    quantity_sent = db.Column(db.Integer, default=0)


### --- Mail Config --- ### 
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'tomtest0606@gmail.com'
app.config['MAIL_PASSWORD'] = 'rdtoyqqxoolahscc'
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEFAULT_SENDER'] = 'tomtest0606@gmail.com'
mail = Mail(app)

### --- Load parts from CSV --- ###
disabled_import = False # placeholder to avoid linter errors
parts_db = []
with open('parts.csv', newline='', encoding='cp1252') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        part_number = row['Product Code'].strip().replace('\u00a0', '').replace('\r', '').replace('\n', '')
        clean_filename = part_number.replace('/', '') + '.png'
        parts_db.append({
            'part_number': part_number,
            'description': row['Description'].strip(),
            'category': row['Category'].strip(),
            'make': row.get('Make', '').strip(),
            'manufacturer': row.get('Manufacturer', '').strip(),
            'image': clean_filename
        })

def get_categories(parts):
    return sorted({part['category'] for part in parts})

### --- APP ROUTES ---- ###
@app.route('/')
def landing():
    return render_template('landing.html')

### --- Catalogue --- ###
@app.route('/catalogue')
def index():
    parts = [p for p in parts_db if 'reagent' not in p['category'].lower()]
    category = request.args.get('category')
    search = request.args.get('search', '').strip().lower()

    filtered_parts = [
        p for p in parts if
        (not category or p['category'] == category) and
        (search in p['part_number'].lower() or
         search in p['description'].lower() or
         search in p['make'].lower() or
         search in p['manufacturer'].lower())
    ] if search else [
        p for p in parts if not category or p['category'] == category
    ]

    return render_template('index.html', parts=filtered_parts,
                           categories=get_categories(parts),
                           selected_category=category, search=search)

### --- Reagents --- ###
@app.route('/reagents')
def reagents():
    parts = [p for p in parts_db if 'reagent' in p['category'].lower()]
    search = request.args.get('search', '').strip().lower()

    filtered_parts = [
        p for p in parts if
        (search in p['part_number'].lower() or
         search in p['description'].lower() or
         search in p['make'].lower() or
         search in p['manufacturer'].lower())
    ] if search else parts

    return render_template('reagents.html', parts=filtered_parts, search=search)


### --- Basket Routes --- ###
@app.route('/parts_basket')
def view_parts_basket():
    return render_template('parts_basket.html', basket=session.get('basket', {}))

@app.route('/reagents_basket')
def view_reagents_basket():
    return render_template('reagents_basket.html', basket=session.get('basket', {}))

@app.route('/add_to_basket/<path:part_number>')
def add_to_basket(part_number):
    part = next((p for p in parts_db if p['part_number'] == part_number), None)
    if not part:
        return redirect(url_for('index'))

    basket = session.get('basket', {})
    if part_number in basket:
        basket[part_number]['quantity'] += 1
    else:
        basket[part_number] = {
            'description': part['description'],
            'category': part['category'],
            'make': part['make'],
            'manufacturer': part['manufacturer'],
            'image': part['image'],
            'quantity': 1
        }
    session['basket'] = basket
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect(request.referrer or url_for('index'))

@app.route('/remove_from_basket/<path:part_number>')
def remove_from_basket(part_number):
    basket = session.get('basket', {})
    basket.pop(part_number, None)
    session['basket'] = basket
    ref = request.referrer or ''
    if 'reagents_basket' in ref:
        return redirect(url_for('view_reagents_basket'))
    return redirect(url_for('view_parts_basket'))

@app.route('/update_quantity/<path:part_number>', methods=['POST'])
def update_quantity(part_number):
    try:
        new_qty = int(request.form.get('quantity', 0))
    except ValueError:
        new_qty = 0

    basket = session.get('basket', {})

    if new_qty <= 0:
        basket.pop(part_number, None)
    else:
        if part_number in basket:
            basket[part_number]['quantity'] = new_qty

    session['basket'] = basket

    ref = request.referrer or ''
    if 'reagents_basket' in ref:
        return redirect(url_for('view_reagents_basket'))
    else:
        return redirect(url_for('view_parts_basket'))

@app.route('/submit_basket', methods=['POST'])
def submit_basket():
    engineer_name = request.form['email_user'].strip()
    engineer_email = f"{engineer_name}@servitech.co.uk".lower()
    source = request.form.get('source', 'catalogue')
    basket = session.get('basket', {})

    if not basket or not engineer_email:
        flash("Your basket is empty or email missing", "warning")
        return redirect(url_for('view_reagents_basket' if source == 'reagents' else 'view_parts_basket'))

    lines = [
        f"• Part Number: {pnum}\n  Description: {item['description']}\n  Quantity: {item['quantity']}"
        for pnum, item in basket.items()
    ]

    comments = request.form.get('comments', '').strip()
    if comments:
        lines.append("\nComments:\n" + comments)

    body_text = (
        f"Engineer: {engineer_email}\n"
        f"Request Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "Requested Items:\n\n" + "\n\n".join(lines) + "\n"
    )

    if source == "reagents":
        subject = f"REAGENTS REQUEST FROM {engineer_email}"
        recipients = ["Purchasing@servitech.co.uk"]
    else:
        subject = f"PARTS REQUEST FROM {engineer_email}"
        recipients = ["StockRequests@servitech.co.uk"]

    msg = Message(subject, recipients=recipients, cc=[engineer_email], body=body_text)
    mail.send(msg)

    if source == 'reagents':
        new_order = ReagentOrder(email=engineer_email, date=datetime.utcnow())
        for pnum, item in basket.items():
            new_order.items.append(
                ReagentOrderItem(
                    part_number=pnum,
                    description=item['description'],
                    quantity=item['quantity']
                )
            )
    else:
        new_order = PartsOrder(email=engineer_email, date=datetime.utcnow())
        for pnum, item in basket.items():
            new_order.items.append(
                PartsOrderItem(
                    part_number=pnum,
                    description=item['description'],
                    quantity=item['quantity']
                )
            )

    db.session.add(new_order)
    db.session.commit()

    session['basket'] = {}
    flash("Your order has been sent!", "success")
    return render_template('confirmation.html')


### --- Reorder --- ###
@app.route('/reorder', methods=['GET', 'POST'])
def reorder():
    email = request.form.get('email_user') if request.method == 'POST' else None
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
            # Normalize date to datetime format string
            if isinstance(order.date, str):
                # strip off any fractional seconds if present
                date_clean = order.date.split('.')[0]
            else:
                date_clean = order.date.strftime("%Y-%m-%d %H:%M:%S")
            items_list = [
                {"part_number": item.part_number, "description": item.description, "quantity": item.quantity}
                for item in order.items
            ]
            orders.append({"date": date_clean, "items": items_list})

    return render_template('reorder.html', email=full_email, orders=orders)

@app.route('/reorder_submit', methods=['POST'])
def reorder_submit():
    email = request.form.get('email')
    idx = int(request.form.get('order_index', 0))
    orders = (
        ReagentOrder.query
        .filter_by(email=email.lower())
        .order_by(ReagentOrder.date.desc())
        .limit(2)
        .all()
    )
    if idx >= len(orders):
        flash("Invalid reorder index.", "danger")
        return redirect(url_for('reorder'))
    sel = orders[idx]
    lines = [f"{i.part_number} – {i.description} x{i.quantity}" for i in sel.items]
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    body = f"Engineer {email} reorders:\n\n" + "\n".join(lines) + f"\n\n[Reordered on {timestamp}]"
    msg = Message(
        subject=f"***TEST REAGENTS REQUEST FROM {email}***",
        recipients=["Purchasing@servitech.co.uk"],
        cc=[email],
        body=body
    )
    mail.send(msg)
    flash("Reorder sent!", "success")
    return render_template('confirmation.html')

@app.route('/reorder_to_basket', methods=['POST'])
def reorder_to_basket():
    email = request.form.get('email')
    idx = int(request.form.get('order_index', 0))
    orders = ReagentOrder.query.filter_by(email=email.lower()).order_by(ReagentOrder.date.desc()).limit(2).all()
    if idx >= len(orders):
        flash("Invalid reorder index.", "danger")
        return redirect(url_for('reorder'))
    sel = orders[idx]
    basket = session.get('basket', {})
    for item in sel.items:
        pnum = item.part_number
        if pnum in basket:
            basket[pnum]['quantity'] += item.quantity
        else:
            basket[pnum] = {'description': item.description, 'quantity': item.quantity, 'category': 'Reagents', 'make': '', 'manufacturer': '', 'image': ''}
    session['basket'] = basket
    flash("Added past order to basket!", "success")
    return redirect(url_for('view_reagents_basket'))

if __name__ == '__main__':
    app.run(debug=True)


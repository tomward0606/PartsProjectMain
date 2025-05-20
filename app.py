from flask import Flask, render_template, request, redirect, url_for, session
from flask_mail import Mail, Message
import os
import csv

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Mail config
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'tomtest0606@gmail.com'
app.config['MAIL_PASSWORD'] = 'rdtoyqqxoolahscc'
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEFAULT_SENDER'] = 'tomtest0606@gmail.com'
mail = Mail(app)

# Load parts from new CSV layout
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
            'make': row['Make'].strip() if row['Make'] else '',
            'manufacturer': row['Manufacturer'].strip() if row['Manufacturer'] else '',
            'image': clean_filename
        })

def get_categories(parts):
    return sorted(set(part['category'] for part in parts))

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/catalogue')
def index():
    parts = [p for p in parts_db if 'reagent' not in p['category'].lower()]
    category = request.args.get('category')
    search = request.args.get('search', '').lower()
    filtered_parts = [p for p in parts if
                      (not category or p['category'] == category) and
                      (search in p['description'].lower() or search in p['part_number'].lower() or search in p['make'].lower() or search in p['manufacturer'].lower())]
    return render_template('index.html', parts=filtered_parts, categories=get_categories(parts), selected_category=category, search=search)

@app.route('/reagents')
def reagents():
    parts = [p for p in parts_db if 'reagent' in p['category'].lower()]
    category = request.args.get('category')
    search = request.args.get('search', '').lower()
    filtered_parts = [p for p in parts if
                      (not category or p['category'] == category) and
                      (search in p['description'].lower() or search in p['part_number'].lower() or search in p['make'].lower() or search in p['manufacturer'].lower())]
    return render_template('reagents.html', parts=filtered_parts, categories=get_categories(parts), selected_category=category, search=search)

@app.route('/add_to_basket/<path:part_number>')
def add_to_basket(part_number):
    part = next((p for p in parts_db if p['part_number'] == part_number), None)
    if not part:
        return redirect(url_for('index'))

    basket = session.get('basket', {})
    if isinstance(basket, list):
        basket = {}

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

    # First, check if this is an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)

    # Otherwise, do a normal redirect
    return redirect(request.referrer or url_for('index'))

@app.route('/basket')
def view_basket():
    return render_template('basket.html', basket=session.get('basket', {}))

@app.route('/remove_from_basket/<path:part_number>')
def remove_from_basket(part_number):
    basket = session.get('basket', {})
    basket.pop(part_number, None)
    session['basket'] = basket
    return redirect(url_for('view_basket'))

@app.route('/submit_basket', methods=['POST'])
def submit_basket():
    engineer_email = request.form['email']
    basket = session.get('basket', {})

    if not basket or not engineer_email:
        return redirect(url_for('view_basket'))

    parts_list = "\n".join([
        f"{part_number.strip()} - {item['description']} ({item['make']} / {item['manufacturer']}) x{item['quantity']}"
        for part_number, item in basket.items()
    ])

    msg = Message('Parts Request',
                  recipients=['jayn@servitech.co.uk'],
                  cc=[engineer_email])
    msg.body = f"Engineer {engineer_email} requests the following parts:\n\n{parts_list}"
    mail.send(msg)

    session['basket'] = {}
    return render_template('confirmation.html')

@app.route('/update_quantity/<path:part_number>', methods=['POST'])
def update_quantity(part_number):
    quantity = int(request.form['quantity'])
    basket = session.get('basket', {})
    if part_number in basket:
        if quantity <= 0:
            basket.pop(part_number)
        else:
            basket[part_number]['quantity'] = quantity
    session['basket'] = basket
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect(url_for('view_basket'))

if __name__ == '__main__':
    app.run(debug=True)

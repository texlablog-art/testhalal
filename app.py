from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
from dotenv import load_dotenv
from bson.objectid import ObjectId
from datetime import datetime
import random
import string
import secrets
import base64
import json
import requests
import os 

load_dotenv()
app = Flask(__name__)
app.secret_key = "secret123"

# MongoDB Connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client['myshop_db']
products_db = db.products
banners_db = db.banners
orders_db = db.orders
promo_db = db['promo_codes']
campaign_db = db['campaigns']
user_collection_db = db['user_collections']

IMGBB_API_KEY = "0bb1747f7045ccee9cc03c792b828a67"

def upload_to_imgbb(file):
    """Uploads a file object to ImgBB and returns the direct link."""
    url = "https://api.imgbb.com/1/upload"
    payload = {"key": IMGBB_API_KEY}
    files = {"image": file.read()}
    response = requests.post(url, payload, files=files)
    if response.status_code == 200:
        return response.json()['data']['url']
    return None

@app.route('/')
def index():
    # Fetching products and banners, sorted by newest first
    products = list(products_db.find().sort("_id", -1))
    banners = list(banners_db.find().sort("_id", -1))
    return render_template('index.html', products=products, banners=banners)

@app.route('/add_to_cart/<id>')
def add_to_cart(id):
    # ১. ইউআরএল থেকে ভেরিয়েন্ট রিসিভ করা ✨
    variant = request.args.get('variant', '').strip()
    
    # ২. সেশনে কার্ট ইনিশিয়ালাইজ করা
    if 'cart' not in session or not isinstance(session['cart'], list):
        session['cart'] = [] # ডিকশনারির বদলে লিস্ট ব্যবহার করা ভেরিয়েন্টের জন্য সহজ
    
    cart = session['cart']
    
    # ৩. প্রোডাক্টটি কি অলরেডি কার্টে আছে (একই আইডি এবং একই ভেরিয়েন্ট)?
    found = False
    for item in cart:
        if item['id'] == id and item['variant'] == variant:
            item['quantity'] += 1
            found = True
            break
    
    # ৪. যদি কার্টে না থাকে, তবে নতুন আইটেম হিসেবে যোগ করা
    if not found:
        cart.append({
            'id': id,
            'variant': variant,
            'quantity': 1
        })
    
    # ৫. সেশন সেভ করা
    session['cart'] = cart
    session.modified = True
    
    # --- DIRECT CHECKOUT LOGIC ---
    next_page = request.args.get('next')
    if next_page == 'checkout':
        return redirect(url_for('checkout'))
    
    flash("Product added to cart!", "success")
    return redirect(request.referrer or url_for('index'))

# --- 1. The Template Filter (Keep at top of app.py) ---
@app.template_filter('last4')
def last4_filter(s):
    # This is a safety fallback in case an order is missing the 10-digit ID
    return str(s)[-6:].upper() if s else "ORDER"

# --- 2. The Corrected Route ---
@app.route('/my-orders', methods=['GET', 'POST'])
def my_orders():
    orders = []
    phone = None
    
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        if phone:
            try:
                # We search your 'orders_db' collection for the 11-digit phone
                # We sort by 'created_at' -1 to show the latest order first
                orders = list(orders_db.find({"phone": phone}).sort("created_at", -1))
                
                if not orders:
                    flash(f"No orders found for {phone}", "info")
            except Exception as e:
                print(f"Database Error: {e}")
                flash("System error. Please try again later.", "danger")
                
    return render_template('my_orders.html', orders=orders, phone=phone)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('user') == '1' and request.form.get('pass') == '1':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/about')
def about():
    # This renders the about.html template
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        # Collect form data
        contact_msg = {
            "name": request.form.get('name'),
            "email": request.form.get('email'),
            "subject": request.form.get('subject'),
            "message": request.form.get('message'),
            "status": "New",
            "date": datetime.now()
        }
        
        # Save to a new collection called 'messages'
        db.messages.insert_one(contact_msg)
        
        flash("Message sent! We will contact you soon.", "success")
        return redirect(url_for('contact'))
        
    return render_template('contact.html')


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    # 1. Fetch Orders and Products (Newest first)
    all_orders = list(orders_db.find().sort("created_at", -1))
    all_products = list(products_db.find())
    
    # 2. Helper to encode order for JavaScript
    def prepare_order_for_js(order):
        # Create a copy so we don't mess up the original list
        o = dict(order)
        # Convert non-serializable objects to strings
        o['_id'] = str(o['_id'])
        if 'created_at' in o:
            o['created_at'] = o['created_at'].strftime('%Y-%m-%d %H:%M')
            
        # Convert to JSON string, then Base64 encode it
        json_data = json.dumps(o)
        return base64.b64encode(json_data.encode()).decode()

    # Add the safe string to each order
    for order in all_orders:
        order['safe_json'] = prepare_order_for_js(order)

    # 3. Calculate Dashboard Stats
    stats = {
        "total_revenue": sum(order.get('total', 0) for order in all_orders if order.get('status') != 'Cancelled'),
        "order_count": len(all_orders),
        "pending_orders": len([o for o in all_orders if o.get('status') == 'Pending']),
        "total_products": len(all_products)
    }
    
    return render_template('admin_dashboard.html', 
                           orders=all_orders, 
                           products=all_products,
                           stats=stats,
                           active_page='dashboard')


# --- ADD & VIEW PRODUCTS ---
@app.route('/admin/add_product', methods=['GET', 'POST'])
def add_product():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        # ১. মেইন ইমেজ হ্যান্ডেল করা
        main_img = request.files.get('main_image')
        main_url = upload_to_imgbb(main_img) if (main_img and main_img.filename != '') else ""
        
        # ২. এক্সট্রা গ্যালারি ইমেজ হ্যান্ডেল করা
        extra_files = request.files.getlist('extra_images') 
        extra_image_urls = []
        for file in extra_files:
            if file and file.filename != '':
                url = upload_to_imgbb(file)
                if url:
                    extra_image_urls.append(url)

        # ৩. ভেরিয়েন্ট ডাটা প্রসেস করা (Size/ML and Price) ✨
        # HTML থেকে আসা variant_name[] এবং variant_price[] লিস্ট রিসিভ করা
        v_names = request.form.getlist('variant_name[]')
        v_prices = request.form.getlist('variant_price[]')
        
        variants = []
        for name, price in zip(v_names, v_prices):
            if name.strip() != "":  # যদি নাম খালি না থাকে
                try:
                    variants.append({
                        "name": name.strip(),
                        "price": int(price) if price else 0
                    })
                except ValueError:
                    continue

        # ৪. সাধারণ ফিল্ডগুলো রিসিভ করা
        video_url = request.form.get('video_url', '').strip()
        title = request.form.get('title')
        category = request.form.get('category')
        description = request.form.get('description', '')
        
        # ডাটা টাইপ কনভার্সন (Price and Old Price)
        try:
            price = int(request.form.get('price', 0))
            del_price = int(request.form.get('del_price', 0)) if request.form.get('del_price') else 0
        except ValueError:
            price = 0
            del_price = 0

        # ৫. ডাটাবেস অবজেক্ট তৈরি করা
        product_data = {
            "title": title,
            "category": category, 
            "description": description,
            "price": price,
            "del_price": del_price,
            "variants": variants,        # ভেরিয়েন্টের লিস্ট এখানে সেভ হবে ✨
            "main_image": main_url,
            "extra_images": extra_image_urls,
            "video_url": video_url,
            "in_stock": True, 
            "created_at": datetime.now()
        }
        
        # ডাটাবেসে ইনসার্ট করা
        products_db.insert_one(product_data)
        
        flash(f"Product '{title}' published successfully!", "success")
        return redirect(url_for('add_product'))

    # GET রিকোয়েস্টের জন্য সব প্রোডাক্ট ফেচ করা (নতুনগুলো আগে থাকবে)
    all_products = list(products_db.find().sort("created_at", -1))
    return render_template('add_product.html', products=all_products)

# TOGGLE STOCK ROUTE
@app.route('/admin/toggle_stock/<id>', methods=['POST'])
def toggle_stock(id):
    product = products_db.find_one({"_id": ObjectId(id)})
    if product:
        # If in_stock exists and is True, make it False. Otherwise make it True.
        new_status = not product.get('in_stock', True)
        products_db.update_one({"_id": ObjectId(id)}, {"$set": {"in_stock": new_status}})
        flash("Stock status updated!", "info")
    return redirect(url_for('add_product'))

# EDIT PRODUCT ROUTE
@app.route('/admin/edit_product/<product_id>', methods=['POST'])
def edit_product(product_id):
    # Get form data
    title = request.form.get('title')
    price = request.form.get('price')
    del_price = request.form.get('del_price')
    category = request.form.get('category')
    video_url = request.form.get('video_url')
    
    # Update MongoDB (example)
    db.products.update_one(
        {'_id': ObjectId(product_id)},
        {'$set': {
            'title': title,
            'price': price,
            'del_price': del_price,
            'category': category,
            'video_url': video_url
        }}
    )
    return redirect('/admin/add_product')

@app.route('/admin/delete_product/<id>', methods=['POST'])
def delete_product(id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    try:
        # Perform the deletion
        products_db.delete_one({"_id": ObjectId(id)})
        flash("Product deleted successfully", "warning")
    except Exception as e:
        flash(f"Error: {e}", "danger")
        
    return redirect(url_for('add_product')) # Redirect back to the inventory list

# --- 2. ADD BANNER ROUTE ---
@app.route('/admin/banners', methods=['GET', 'POST'])
def add_banner():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        banner_file = request.files.get('banner_image')
        banner_url = upload_to_imgbb(banner_file) if banner_file else ""
        
        if banner_url:
            banners_db.insert_one({
                "image_url": banner_url,
                "created_at": datetime.now()
            })
            flash("Banner uploaded successfully!", "success")
        else:
            flash("Failed to upload banner.", "danger")
        
        return redirect(request.referrer)

    # Fetch existing banners to show on the same page
    all_banners = list(banners_db.find().sort("created_at", -1))
    return render_template('banners.html', banners=all_banners)

@app.route('/admin/delete_banner/<id>', methods=['POST'])
def delete_banner(id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    from bson import ObjectId
    banners_db.delete_one({"_id": ObjectId(id)})
    
    flash("Banner deleted successfully", "warning")
    return redirect(url_for('add_banner')) # Or wherever your banner route is

@app.route('/admin/promo')
def admin_promo():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    # Fetch data for the page
    coupons = list(promo_db.find().sort("created_at", -1))
    products = list(products_db.find({}, {"title": 1})) # Get only titles for the dropdown
    
    return render_template('admin_promo.html', 
                           coupons=coupons, 
                           products=products, 
                           active_page='promo') # This triggers the 'active' class

# 1. Admin: Save Promo
@app.route('/admin/add_promo', methods=['POST'])
def add_promo():
    code = request.form.get('code').upper().strip()
    discount = int(request.form.get('discount', 0))
    # Get multiple selected product IDs from dropdown
    applicable_prods = request.form.getlist('products') 

    promo_db.insert_one({
        "code": code,
        "discount_percent": discount,
        "applicable_products": applicable_prods,
        "created_at": datetime.now()
    })
    flash("Promo code created!", "success")
    return redirect('/admin/promo')

@app.route('/admin/delete_promo/<promo_id>', methods=['POST'])
def delete_promo(promo_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    from bson import ObjectId
    promo_db.delete_one({"_id": ObjectId(promo_id)})
    flash("Promo code removed!", "info")
    return redirect('/admin/promo')

# 2. Checkout: Apply Promo (AJAX or Form)
@app.route('/apply_promo', methods=['POST'])
def apply_promo():
    code_input = request.form.get('promo_code').upper().strip()
    cart = session.get('cart', []) # Assuming cart is list of product IDs
    
    promo = promo_db.find_one({"code": code_input})
    
    if not promo:
        return jsonify({"success": False, "message": "Invalid Code"})

    # Check if the promo applies to items in the cart
    # If 'all' is in list, it applies. Otherwise check if cart items match promo list.
    can_apply = False
    if "all" in promo['applicable_products']:
        can_apply = True
    else:
        # Check if at least one item in cart is allowed for this promo
        for item_id in cart:
            if item_id in promo['applicable_products']:
                can_apply = True
                break

    if can_apply:
        session['applied_promo'] = {
            "code": promo['code'],
            "discount": promo['discount_percent']
        }
        return jsonify({"success": True, "discount": promo['discount_percent']})
    
    return jsonify({"success": False, "message": "Code not applicable to items in cart"})

# --- 3. ORDER ACTION (Confirm/Cancel) ---
@app.route('/admin/order_action/<id>/<status>')
def order_action(id, status):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    # Status is passed from HTML as 'Confirmed' or 'Cancelled'
    orders_db.update_one(
        {"_id": ObjectId(id)}, 
        {"$set": {"status": status}}
    )
    flash(f"Order marked as {status}", "info")
    return redirect(url_for('admin_dashboard'))

# --- 4. DELETE ORDER ---
@app.route('/admin/delete_order/<id>')
def delete_order(id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    orders_db.delete_one({"_id": ObjectId(id)})
    flash("Order record deleted.", "danger")
    return redirect(url_for('admin_dashboard'))

# --- 6. LOGOUT ---
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/product/<id>')
def product_details(id):
    try:
        # ১. আইডি ভ্যালিড কিনা চেক করা (প্রিভেন্টিভ মেজার)
        if not ObjectId.is_valid(id):
            return "Invalid Product ID format", 400
            
        product = products_db.find_one({"_id": ObjectId(id)})
        
        if product:
            # ২. ডিফল্ট ভেরিয়েন্ট চেক (যদি কোনো প্রোডাক্টে ভেরিয়েন্ট না থাকে)
            if 'variants' not in product:
                product['variants'] = []
                
            return render_template('product_details.html', p=product)
        else:
            flash("Product not found!", "danger")
            return redirect(url_for('index')) # সরাসরি হোমপেজে পাঠিয়ে দেওয়া ভালো
            
    except Exception as e:
        print(f"Detailed Error: {e}")
        return "An internal error occurred", 500

@app.route('/category/<cat_name>')
def category_page(cat_name):
    # This query finds the category regardless of CAPITALIZATION
    # Example: 'Fashion', 'fashion', and 'FASHION' all work.
    query = {"category": {"$regex": f"^{cat_name}$", "$options": "i"}}
    
    products = list(products_db.find(query).sort("_id", -1))
    
    # Capitalize for the page heading (e.g., 'gadgets' -> 'Gadgets')
    display_title = cat_name.replace('-', ' ').capitalize()
    
    return render_template('category.html', products=products, title=display_title)

@app.context_processor
def inject_theme():
    try:
        settings = db.settings.find_one({"type": "site_config"})
        theme = settings.get('theme', 'default') if settings else 'default'
    except Exception:
        theme = 'default'
    return dict(current_theme=theme)

@app.route('/admin/themes')
def admin_themes():
    settings = db.settings.find_one({"type": "site_config"})
    theme = settings.get('theme', 'default') if settings else 'default'
    # active_page='themes' যোগ করা হয়েছে যাতে সাইডবারে লিঙ্কটি নীল হয়ে থাকে
    return render_template('admin_themes.html', current_theme=theme, active_page='themes')

@app.route('/admin/update-theme', methods=['POST'])
def update_theme():
    new_theme = request.form.get('theme')
    
    # থিমের নাম কি খালি? তবে ডিফল্ট সেট করুন
    if not new_theme:
        new_theme = 'default'
        
    try:
        # ডাটাবেজে আপডেট বা ইনসার্ট করা
        db.settings.update_one(
            {"type": "site_config"},
            {"$set": {"theme": new_theme}},
            upsert=True
        )
        flash(f"Store vibe successfully changed to {new_theme.capitalize()}!", "success")
    except Exception as e:
        flash("Error updating theme: " + str(e), "danger")
        
    return redirect(url_for('admin_themes'))

@app.errorhandler(404)
def page_not_found(e):
    # This catches "Page Not Found" errors
    return render_template('error.html', error_code="404", message="Oops! This page has vanished into thin air."), 404

@app.errorhandler(500)
def server_error(e):
    # This catches "Server Crashed" errors
    return render_template('error.html', error_code="500", message="Something went wrong on our end. We're fixing it!"), 500

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy.html')

def generate_order_id():
    """Generates a unique 10-digit alphanumeric ID (A-Z, 0-9)."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    # ১. সেশন থেকে কার্ট নেওয়া (এখন এটি একটি LIST)
    cart_list = session.get('cart', [])
    
    if not cart_list or not isinstance(cart_list, list):
        flash("Your cart is empty!", "warning")
        return redirect(url_for('index'))

    items_for_summary = []
    grand_total = 0
    
    # ২. কার্টের প্রতিটি আইটেম প্রসেস করা ✨
    for item in cart_list:
        pid = item.get('id')
        qty = item.get('quantity', 1)
        selected_variant = item.get('variant') # ভেরিয়েন্ট নাম (যেমন: 100ml)
        
        product = products_db.find_one({"_id": ObjectId(pid)})
        
        if product:
            # ডিফল্ট প্রাইস
            price = int(product.get('price', 0))
            
            # ✨ যদি ভেরিয়েন্ট থাকে, তবে সেই ভেরিয়েন্টের স্পেসিফিক প্রাইস নেওয়া
            if selected_variant and 'variants' in product:
                for v in product['variants']:
                    if v['name'] == selected_variant:
                        price = int(v['price'])
                        break
            
            subtotal = price * qty
            items_for_summary.append({
                "product_id": str(pid),
                "title": product['title'],
                "variant": selected_variant, # ভেরিয়েন্ট সেভ করা হচ্ছে ✨
                "price": price,
                "quantity": qty,
                "subtotal": subtotal,
                "image": product.get('main_image') # HTML এর সাথে সামঞ্জস্য রাখতে 'image' কি (key) ব্যবহার করা হয়েছে
            })
            grand_total += subtotal

    # ৩. অর্ডার সাবমিশন (POST Method)
    if request.method == 'POST':
        alphabet = string.ascii_uppercase + string.digits
        order_number = ''.join(secrets.choice(alphabet) for i in range(10))
        
        # হিডেন ইনপুট থেকে ডাটা নেওয়া
        final_payable = request.form.get('final_total')
        discount_amount = request.form.get('discount_amount', '0')
        promo_used = request.form.get('applied_promo', '')

        final_total = float(final_payable) if final_payable else grand_total
        pay_method = request.form.get('payment_method')
        trx_id = request.form.get('transaction_id', 'N/A')
        
        order_data = {
            "order_id": order_number,
            "name": request.form.get('name'),
            "phone": request.form.get('phone'),
            "address": request.form.get('address'),
            "items_details": items_for_summary, # এখানে ভেরিয়েন্ট সহ সব ডাটা থাকছে
            "subtotal": grand_total,
            "discount": float(discount_amount),
            "promo_code": promo_used,
            "total": final_total,
            "payment_method": "Cash on Delivery" if pay_method == 'cod' else "Online Payment",
            "transaction_id": trx_id,
            "status": "Pending",
            "created_at": datetime.now()
        }
        
        # ডাটাবেসে সেভ এবং সেশন ক্লিয়ার
        orders_db.insert_one(order_data)
        session.pop('cart', None)
        session.pop('applied_promo', None)
        
        return render_template('order_success.html', order_id=order_number)

    # ৪. পেজ রেন্ডার করা (GET Method)
    return render_template('checkout.html', items=items_for_summary, total=grand_total)

# --- USER TRACK ROUTE ---
@app.route('/track', methods=['GET', 'POST'])
def track_order():
    order = None
    if request.method == 'POST':
        order_id = request.form.get('order_id').strip().upper()
        order = orders_db.find_one({"order_id": order_id})
        if not order:
            flash("Order ID not found.", "danger")
    return render_template('track.html', order=order)

# --- ADMIN TRACK MANAGEMENT (FIXED) ---
@app.route('/admin/track_manage/<id>', methods=['POST'])
def track_manage(id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    new_status = request.form.get('status').strip() # .strip() যোগ করা হয়েছে স্পেস সরানোর জন্য
    
    # বর্তমান অর্ডারটি চেক করা হচ্ছে এটি ইতিমধ্যে এই স্ট্যাটাসে আছে কি না
    current_order = orders_db.find_one({"_id": ObjectId(id)})
    
    # স্ট্যাটাস যদি আগেরটাই থাকে, তবে নতুন করে হিস্ট্রিতে পুশ করার দরকার নেই
    if current_order and current_order.get('status') == new_status:
        flash(f"Order is already marked as {new_status}", "info")
        return redirect(request.referrer or url_for('admin_track_list'))

    # Update the order status and history log
    orders_db.update_one(
        {"_id": ObjectId(id)},
        {
            "$set": {"status": new_status},
            "$push": {
                "history": {
                    "status": new_status,
                    "time": datetime.now().strftime("%I:%M %p, %d %b %Y")
                }
            }
        }
    )
    
    flash(f"Order updated to {new_status}", "success")
    return redirect(request.referrer or url_for('admin_track_list'))

@app.route('/admin/track')
def admin_track_list():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    # Fetch all orders, newest first
    orders = list(orders_db.find().sort("_id", -1))
    return render_template('admin_track.html', orders=orders)

# --- VIEW CART ---
@app.route('/cart')
def view_cart():
    # কার্ট এখন একটি LIST অফ ডিকশনারি: [{'id': '...', 'qty': 1, 'variant': '100ml'}, ...]
    cart_list = session.get('cart', [])
    
    # যদি পুরনো ডিকশনারি ফরম্যাট থাকে, তবে ক্লিয়ার করে লিস্ট করে দেবে
    if isinstance(cart_list, dict):
        session['cart'] = []
        cart_list = []

    items_to_show = []
    total = 0
    
    for index, item in enumerate(cart_list):
        pid = item.get('id')
        qty = item.get('quantity', 1)
        selected_variant = item.get('variant')
        
        try:
            product = products_db.find_one({"_id": ObjectId(pid)})
            if product:
                # ১. প্রাইস ক্যালকুলেশন (ভেরিয়েন্ট থাকলে সেই প্রাইস নেবে)
                price = int(product.get('price', 0))
                if selected_variant and 'variants' in product:
                    for v in product['variants']:
                        if v['name'] == selected_variant:
                            price = int(v['price'])
                            break
                
                subtotal = price * qty
                total += subtotal
                
                # HTML এ দেখানোর জন্য ডাটা গোছানো
                item_data = {
                    "index": index, # ইনডেক্স দিয়ে আপডেট করা সহজ
                    "_id": pid,
                    "title": product['title'],
                    "main_image": product.get('main_image'),
                    "category": product.get('category'),
                    "price": price,
                    "quantity": qty,
                    "subtotal": subtotal,
                    "variant": selected_variant,
                    "all_variants": product.get('variants', []) # ড্রপডাউনের জন্য
                }
                items_to_show.append(item_data)
        except Exception as e:
            print(f"Error: {e}")
            
    return render_template('cart.html', items=items_to_show, total=total)

# ২. ভেরিয়েন্ট আপডেট করার নতুন রুট ✨
@app.route('/update_variant/<int:index>', methods=['POST'])
def update_variant(index):
    cart = session.get('cart', [])
    new_variant = request.form.get('new_variant')
    
    if 0 <= index < len(cart):
        cart[index]['variant'] = new_variant
        session.modified = True
        flash("Variant updated!", "success")
    return redirect(url_for('view_cart'))

# ৩. আপডেট কার্ট (ইনডেক্স অনুযায়ী)
@app.route('/update_cart/<int:index>/<action>')
def update_cart(index, action):
    cart = session.get('cart', [])
    if 0 <= index < len(cart):
        if action == 'plus':
            cart[index]['quantity'] += 1
        elif action == 'minus' and cart[index]['quantity'] > 1:
            cart[index]['quantity'] -= 1
        session.modified = True
    return redirect(url_for('view_cart'))

# ৪. রিমুভ (ইনডেক্স অনুযায়ী)
@app.route('/remove_from_cart/<int:index>')
def remove_from_cart(index):
    cart = session.get('cart', [])
    if 0 <= index < len(cart):
        cart.pop(index)
        session.modified = True
        flash("Item removed.")
    return redirect(url_for('view_cart'))

if __name__ == '__main__':

    app.run(debug=True)


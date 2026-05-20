from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import json, uuid, base64, os, time, threading
from datetime import datetime, timedelta
from config import *

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # 1 year static cache
app.config["JSON_SORT_KEYS"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = False

# ── Gzip compression for fast mobile loading ──
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass  # Optional — install: pip install flask-compress

# ─────────────────────────────────────────────
#  SMART CACHE — Google Sheets rate limit fix
# ─────────────────────────────────────────────
_cache = {}           # {key: {data, ts}}
_cache_lock = threading.Lock()

CACHE_TTL = {
    "menu":     300,   # 5 min  — menu changes rarely
    "orders":   8,     # 8 sec  — orders need to be fresh
    "payments": 8,     # 8 sec  — fast now with persistent client
    "about":    120,   # 2 min
    "stats":    8,     # 8 sec — dashboard stats
}

# ── Persistent Google Sheets client — avoid re-auth every request ──
_gs_client = None
_gs_lock   = threading.Lock()

def get_fast_worksheet(sheet_name):
    """Reuse same gspread client — 10x faster than reconnecting"""
    global _gs_client
    try:
        with _gs_lock:
            if _gs_client is None:
                _gs_client = get_sheet_client()
        spreadsheet = _gs_client.open_by_key(SPREADSHEET_ID)
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        # Token expired — reconnect once
        try:
            with _gs_lock:
                _gs_client = get_sheet_client()
            spreadsheet = _gs_client.open_by_key(SPREADSHEET_ID)
            return spreadsheet.worksheet(sheet_name)
        except Exception as e:
            print(f"Sheet error [{sheet_name}]: {e}")
            return None

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and (time.time() - item["ts"]) < item["ttl"]:
            return item["data"]
    return None

def cache_set(key, data, ttl=10):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time(), "ttl": ttl}

def cache_del(key):
    with _cache_lock:
        _cache.pop(key, None)

def cache_del_prefix(prefix):
    with _cache_lock:
        keys = [k for k in _cache if k.startswith(prefix)]
        for k in keys:
            _cache.pop(k, None)

def cached_sheet(sheet_name, ttl=10):
    """Get sheet data with cache — uses persistent client"""
    key = f"sheet:{sheet_name}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    ws = get_fast_worksheet(sheet_name)
    data = rows_to_dicts(ws) if ws else []
    cache_set(key, data, ttl)
    return data

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def short_id():
    return str(uuid.uuid4())[:8].upper()

def safe_float(v, default=0.0):
    try: return float(v)
    except: return default

def rows_to_dicts(ws):
    if not ws: return []
    try:
        records = ws.get_all_records()
        return records
    except: return []

# ─────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        if role == "admin":
            username = request.form.get("username","")
            password = request.form.get("password","")
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session["role"] = "admin"
                session["username"] = username
                return jsonify({"success": True, "redirect": url_for("admin_dashboard")})
            return jsonify({"success": False, "message": "Username ama Password khalad ah!"})
        elif role == "customer":
            session["role"] = "customer"
            return jsonify({"success": True, "redirect": url_for("customer_home")})
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") not in ("admin","customer"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────
#  ADMIN — DASHBOARD
# ─────────────────────────────────────────────
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin/dashboard.html")

@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    period = request.args.get("period","today")
    category = request.args.get("category","all")
    offset = int(request.args.get("offset", 0))

    # Use cache — only fetch from Sheets every 10 seconds
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["stats"])

    now = datetime.now()

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0) + timedelta(days=offset)
        end   = start + timedelta(days=1)
    elif period == "week":
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0)
        start = start_of_week + timedelta(weeks=offset)
        end   = start + timedelta(weeks=1)
    elif period == "month":
        month_start = now.replace(day=1, hour=0, minute=0, second=0)
        target_month = month_start.month + offset
        target_year  = month_start.year
        while target_month > 12: target_month -= 12; target_year += 1
        while target_month < 1:  target_month += 12; target_year -= 1
        start = month_start.replace(month=target_month, year=target_year)
        if target_month == 12:
            end = start.replace(year=target_year+1, month=1)
        else:
            end = start.replace(month=target_month+1)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0)
        start = start.replace(year=start.year + offset)
        end   = start.replace(year=start.year+1)
    else:
        start = now.replace(hour=0, minute=0, second=0)
        end   = start + timedelta(days=1)

    filtered = []
    for o in orders:
        try:
            odate = datetime.strptime(str(o.get("Date",""))[:19], "%Y-%m-%d %H:%M:%S")
            if start <= odate < end:
                if category == "all" or str(o.get("Items","")).lower().find(category.lower()) >= 0:
                    filtered.append(o)
        except: pass

    total_revenue = sum(safe_float(o.get("Final_Total",0)) for o in filtered)
    total_orders  = len(filtered)

    # category breakdown
    breakdown = {"food":0,"shopping":0,"khudaar":0,"raashin":0}
    for o in filtered:
        items_str = str(o.get("Items","")).lower()
        for cat in breakdown:
            if cat in items_str:
                breakdown[cat] += safe_float(o.get("Final_Total",0))

    return jsonify({
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "breakdown": breakdown,
        "orders": filtered,
        "period_label": f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}"
    })

# ─────────────────────────────────────────────
#  ADMIN — NOTIFICATIONS / ORDERS
# ─────────────────────────────────────────────
@app.route("/api/notifications")
@admin_required
def notifications():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    pending = [o for o in orders if str(o.get("Status","")).strip().lower() == "pending"]
    return jsonify({"count": len(pending), "orders": pending})

@app.route("/api/order/accept", methods=["POST"])
@admin_required
def accept_order():
    data = request.json
    order_id = data.get("order_id")
    ws = get_worksheet(ORDERS)
    records = ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            # Find Status column index
            headers = ws.row_values(1)
            status_col = headers.index("Status") + 1
            ws.update_cell(i, status_col, "Processed")
            cache_del_prefix("sheet:")  # bust all order/payment caches

            # Also log to Payments
            pws = get_worksheet(PAYMENTS)
            pid = short_id()
            pws.append_row([pid, order_id, row.get("Final_Total",""), row.get("Items",""), now_str(), datetime.now().strftime("%H:%M:%S"), "Paid"])
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "Order not found"})

@app.route("/api/order/decline", methods=["POST"])
@admin_required
def decline_order():
    data = request.json
    order_id = data.get("order_id")
    ws = get_worksheet(ORDERS)
    records = ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            ws.delete_rows(i)
            cache_del_prefix("sheet:")  # bust cache
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/order/status", methods=["POST"])
@admin_required
def update_order_status():
    data = request.json
    order_id = data.get("order_id")
    status   = data.get("status")
    ws = get_worksheet(ORDERS)
    records = ws.get_all_records()
    headers = ws.row_values(1)
    status_col = headers.index("Status") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            ws.update_cell(i, status_col, status)
            cache_del_prefix("sheet:")  # bust cache
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/order/create", methods=["POST"])
@admin_required
def create_order():
    data = request.json
    order_id = "ORD-" + short_id()
    items_json = json.dumps(data.get("items", []))
    total      = safe_float(data.get("total", 0))
    discount   = safe_float(data.get("discount", 0))
    final      = total - (total * discount / 100)

    ws = get_worksheet(ORDERS)
    cache_del_prefix("sheet:")  # bust cache before write
    ws.append_row([
        order_id,
        data.get("customer_name","Admin Order"),
        data.get("phone",""),
        data.get("city",""),
        data.get("location",""),
        data.get("district",""),
        data.get("sender_name",""),
        items_json,
        total,
        discount,
        round(final,2),
        "Pending",
        data.get("payment_method","Cash"),
        now_str(),
        datetime.now().strftime("%H:%M:%S")
    ])
    return jsonify({"success": True, "order_id": order_id})

@app.route("/admin/live-orders")
@admin_required
def admin_live_orders():
    return render_template("admin/live_order.html")

@app.route("/api/delivered-orders")
@admin_required
def delivered_orders():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    delivered = [o for o in orders if str(o.get("Status","")).lower() not in ("pending","")]
    return jsonify({"orders": delivered})

@app.route("/api/all-orders")
@admin_required
def all_orders():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    return jsonify({"orders": orders})

# ─────────────────────────────────────────────
#  ADMIN — MENU
# ─────────────────────────────────────────────
@app.route("/admin/menu")
@admin_required
def admin_menu():
    return render_template("admin/menu.html")

CATEGORY_SHEETS = {
    "food": FOOD_MENU,
    "shopping": SHOPPING_MENU,
    "khudaar": KHUDAAR_MENU,
    "raashin": RAASHIN_MENU
}

@app.route("/api/menu/<category>")
def get_menu(category):
    sheet_name = CATEGORY_SHEETS.get(category.lower())
    if not sheet_name:
        return jsonify({"items": []})
    items = cached_sheet(sheet_name, ttl=CACHE_TTL["menu"])
    return jsonify({"items": items})

@app.route("/api/menu/add", methods=["POST"])
@admin_required
def add_menu_item():
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "message": "No data received"})
        category = data.get("category","").lower()
        sheet_name = CATEGORY_SHEETS.get(category)
        if not sheet_name:
            return jsonify({"success": False, "message": f"Category not found: {category}"})
        ws = get_worksheet(sheet_name)
        if not ws:
            return jsonify({"success": False, "message": "Google Sheet connection failed"})
        records = ws.get_all_records()
        new_id = len(records) + 1
        image_url = data.get("image_url", "")
        # Google Sheets cell limit is ~50000 chars — truncate safely
        if len(str(image_url)) > 45000:
            image_url = str(image_url)[:45000]
        ws.append_row([
            new_id,
            data.get("name",""),
            float(data.get("price", 0)),
            image_url,
            category.capitalize(),
            today_str()
        ])
        cache_del(f"sheet:{sheet_name}")  # bust menu cache
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        print(f"add_menu_item ERROR: {e}")
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/menu/delete", methods=["POST"])
@admin_required
def delete_menu_item():
    data = request.json
    category  = data.get("category","").lower()
    item_id   = str(data.get("id",""))
    sheet_name = CATEGORY_SHEETS.get(category)
    if not sheet_name:
        return jsonify({"success": False})
    ws = get_worksheet(sheet_name)
    records = ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if str(row.get("ID","")) == item_id:
            ws.delete_rows(i)
            cache_del(f"sheet:{sheet_name}")  # bust menu cache
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "Item not found"})

@app.route("/api/menu/upload-image", methods=["POST"])
@admin_required
def upload_image():
    """Upload image to Imgur and return public URL"""
    import requests as req
    file = request.files.get("image")
    if not file:
        return jsonify({"success": False, "message": "No image"})
    try:
        image_data = base64.b64encode(file.read()).decode("utf-8")
        # Imgur Client-ID (free, public upload)
        headers = {"Authorization": "Client-ID 546c25a59c58ad7"}
        response = req.post(
            "https://api.imgur.com/3/image",
            headers=headers,
            data={"image": image_data, "type": "base64"}
        )
        result = response.json()
        if result.get("success"):
            url = result["data"]["link"]
            return jsonify({"success": True, "url": url})
        else:
            # Fallback: save locally and serve
            import uuid, os
            ext = file.filename.rsplit(".",1)[-1] if "." in file.filename else "jpg"
            fname = str(uuid.uuid4())[:8] + "." + ext
            save_path = os.path.join(BASE_DIR, "static", "images", fname)
            # re-read not possible after b64encode, return error
            return jsonify({"success": False, "message": "Imgur upload failed: " + str(result)})
    except Exception as e:
        print(f"Image upload error: {e}")
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/menu/upload-image-local", methods=["POST"])
@admin_required  
def upload_image_local():
    """Save image locally to static/images/ folder"""
    import uuid, os
    file = request.files.get("image")
    if not file:
        return jsonify({"success": False, "message": "No image"})
    try:
        ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else "jpg"
        if ext not in ["jpg","jpeg","png","gif","webp"]:
            ext = "jpg"
        fname = "menu_" + str(uuid.uuid4())[:8] + "." + ext
        save_dir = os.path.join(BASE_DIR, "static", "images")
        os.makedirs(save_dir, exist_ok=True)
        file.save(os.path.join(save_dir, fname))
        url = f"/static/images/{fname}"
        return jsonify({"success": True, "url": url})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# ─────────────────────────────────────────────
#  ADMIN — PAYMENTS
# ─────────────────────────────────────────────
@app.route("/admin/payments")
@admin_required
def admin_payments():
    return render_template("admin/payments.html")

@app.route("/api/payments")
@admin_required
def get_payments():
    period   = request.args.get("period","today")
    offset   = int(request.args.get("offset", 0))
    payments = cached_sheet(PAYMENTS, ttl=CACHE_TTL["payments"])
    now      = datetime.now()

    if period == "today":
        start = (now + timedelta(days=offset)).replace(hour=0,minute=0,second=0)
        end   = start + timedelta(days=1)
    elif period == "week":
        s = now - timedelta(days=now.weekday())
        s = s.replace(hour=0,minute=0,second=0)
        start = s + timedelta(weeks=offset)
        end   = start + timedelta(weeks=1)
    elif period == "month":
        s = now.replace(day=1,hour=0,minute=0,second=0)
        m = s.month + offset; y = s.year
        while m > 12: m -= 12; y += 1
        while m < 1:  m += 12; y -= 1
        start = s.replace(month=m, year=y)
        end   = start.replace(month=m+1) if m < 12 else start.replace(year=y+1,month=1)
    elif period == "year":
        start = now.replace(month=1,day=1,hour=0,minute=0,second=0,year=now.year+offset)
        end   = start.replace(year=start.year+1)
    else:
        start = now.replace(hour=0,minute=0,second=0)
        end   = start + timedelta(days=1)

    filtered = []
    for p in payments:
        try:
            d = datetime.strptime(str(p.get("Date",""))[:19], "%Y-%m-%d %H:%M:%S")
            if start <= d < end:
                filtered.append(p)
        except: pass

    total = sum(safe_float(p.get("Amount",0)) for p in filtered)
    return jsonify({"payments": filtered, "total": total})

@app.route("/api/payments/edit", methods=["POST"])
@admin_required
def edit_payment():
    data = request.json
    payment_id = str(data.get("payment_id",""))
    new_amount = data.get("amount")
    ws = get_worksheet(PAYMENTS)
    records = ws.get_all_records()
    headers = ws.row_values(1)
    amount_col = headers.index("Amount") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Payment_ID","")) == payment_id:
            ws.update_cell(i, amount_col, new_amount)
            cache_del(f"sheet:{PAYMENTS}")  # bust payments cache
            return jsonify({"success": True})
    return jsonify({"success": False})

# ─────────────────────────────────────────────
#  ADMIN — ABOUT
# ─────────────────────────────────────────────
@app.route("/admin/about")
@admin_required
def admin_about():
    return render_template("admin/about.html")

@app.route("/api/about", methods=["GET"])
def get_about():
    records = cached_sheet(ABOUT, ttl=CACHE_TTL["about"])
    content = records[0].get("Content","") if records else ""
    return jsonify({"content": content})

@app.route("/api/about", methods=["POST"])
@admin_required
def save_about():
    data = request.json
    content = data.get("content","")
    ws = get_worksheet(ABOUT)
    records = ws.get_all_records()
    if records:
        ws.update_cell(2, 1, content)
    else:
        ws.append_row([content])
    cache_del(f"sheet:{ABOUT}")
    return jsonify({"success": True})

# ─────────────────────────────────────────────
#  CUSTOMER — PAGES
# ─────────────────────────────────────────────
@app.route("/customer/home")
@customer_required
def customer_home():
    return render_template("customer/home.html")

@app.route("/customer/menu")
@customer_required
def customer_menu():
    return render_template("customer/menu.html")

@app.route("/customer/message")
@customer_required
def customer_message():
    return render_template("customer/message.html")

@app.route("/customer/call-center")
@customer_required
def customer_call_center():
    return render_template("customer/call_center.html")

@app.route("/customer/about")
@customer_required
def customer_about():
    return render_template("customer/about.html")

# ─────────────────────────────────────────────
#  CUSTOMER — ORDERS
# ─────────────────────────────────────────────
@app.route("/api/customer/order", methods=["POST"])
@customer_required
def customer_place_order():
    data = request.json
    order_id = "ORD-" + short_id()
    items_json = json.dumps(data.get("items",[]))
    total = safe_float(data.get("total",0))

    ws = get_worksheet(ORDERS)
    cache_del_prefix("sheet:")  # bust cache before write
    ws.append_row([
        order_id,
        data.get("full_name",""),
        data.get("phone",""),
        data.get("city",""),
        data.get("location",""),
        data.get("district",""),
        data.get("sender_name",""),
        items_json,
        total,
        0,
        total,
        "Pending",
        data.get("payment_method",""),
        now_str(),
        datetime.now().strftime("%H:%M:%S")
    ])
    return jsonify({"success": True, "order_id": order_id})

@app.route("/api/customer/orders")
@customer_required
def customer_orders():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    return jsonify({"orders": orders})

@app.route("/api/customer/received", methods=["POST"])
@customer_required
def customer_received():
    data = request.json
    order_id = data.get("order_id")
    ws = get_worksheet(ORDERS)
    records = ws.get_all_records()
    headers = ws.row_values(1)
    status_col = headers.index("Status") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            ws.update_cell(i, status_col, "Delivery Confirmed")
            cache_del_prefix("sheet:")
            return jsonify({"success": True})
    return jsonify({"success": False})

# ─────────────────────────────────────────────
#  CUSTOMER — MESSAGE
# ─────────────────────────────────────────────
@app.route("/api/message/send", methods=["POST"])
@customer_required
def send_message():
    data = request.json
    msg = data.get("message","")
    ws = get_worksheet(MESSAGES)
    records = ws.get_all_records()
    mid = len(records) + 1
    ws.append_row([mid, msg, today_str(), datetime.now().strftime("%H:%M:%S")])
    cache_del(f"sheet:{MESSAGES}")
    return jsonify({"success": True})

# ─────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
#  PWA — Service Worker Route
# ─────────────────────────────────────────────
@app.route("/sw.js")
def service_worker():
    from flask import send_from_directory
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

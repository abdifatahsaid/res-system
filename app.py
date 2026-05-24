from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import json, uuid, base64, os, time, threading
from datetime import datetime, timedelta
from config import *

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
app.config["JSON_SORT_KEYS"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = False

try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# ═══════════════════════════════════════════════════════
#  ULTRA-FAST CACHE SYSTEM
# ═══════════════════════════════════════════════════════
_cache      = {}
_cache_lock = threading.Lock()

CACHE_TTL = {
    "menu":     600,   # 10 min — menu almost never changes
    "orders":   5,     # 5 sec  — fresh orders
    "payments": 5,     # 5 sec
    "about":    300,   # 5 min
    "stats":    5,     # 5 sec
}

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
        for k in keys: _cache.pop(k, None)

# ═══════════════════════════════════════════════════════
#  PERSISTENT SPREADSHEET — single connection reused
# ═══════════════════════════════════════════════════════
_spreadsheet     = None
_spreadsheet_ts  = 0
_spreadsheet_ttl = 3600  # re-auth every 1 hour
_spreadsheet_lock = threading.Lock()

def get_spreadsheet_fast():
    global _spreadsheet, _spreadsheet_ts
    now = time.time()
    with _spreadsheet_lock:
        if _spreadsheet is None or (now - _spreadsheet_ts) > _spreadsheet_ttl:
            try:
                client = get_sheet_client()
                _spreadsheet    = client.open_by_key(SPREADSHEET_ID)
                _spreadsheet_ts = now
            except Exception as e:
                print(f"Spreadsheet connect error: {e}")
                return None
    return _spreadsheet

def get_fast_worksheet(sheet_name):
    """Millisecond worksheet access — reuses persistent connection"""
    try:
        sp = get_spreadsheet_fast()
        if sp is None: return None
        return sp.worksheet(sheet_name)
    except Exception:
        # Force reconnect once
        global _spreadsheet
        with _spreadsheet_lock:
            _spreadsheet = None
        try:
            sp = get_spreadsheet_fast()
            return sp.worksheet(sheet_name) if sp else None
        except Exception as e:
            print(f"Worksheet error [{sheet_name}]: {e}")
            return None

# ═══════════════════════════════════════════════════════
#  WORKSHEET OBJECT CACHE — avoid repeated .worksheet() calls
# ═══════════════════════════════════════════════════════
_ws_cache      = {}
_ws_cache_lock = threading.Lock()

def get_cached_ws(sheet_name):
    """Cache the worksheet object itself — ultra fast"""
    with _ws_cache_lock:
        ws = _ws_cache.get(sheet_name)
        if ws is not None:
            return ws
    ws = get_fast_worksheet(sheet_name)
    if ws:
        with _ws_cache_lock:
            _ws_cache[sheet_name] = ws
    return ws

def bust_ws_cache():
    with _ws_cache_lock:
        _ws_cache.clear()

def cached_sheet(sheet_name, ttl=10):
    """Get sheet DATA with cache — <5ms on cache hit"""
    key = f"sheet:{sheet_name}"
    hit = cache_get(key)
    if hit is not None:
        return hit  # Cache hit — 0ms
    # Cache miss — fetch from Sheets
    ws   = get_cached_ws(sheet_name)
    data = []
    if ws:
        try:
            data = ws.get_all_records()
        except Exception:
            # Worksheet object stale — refresh
            bust_ws_cache()
            ws = get_fast_worksheet(sheet_name)
            if ws:
                try: data = ws.get_all_records()
                except: data = []
    cache_set(key, data, ttl)
    return data

# ═══════════════════════════════════════════════════════
#  BACKGROUND PRE-LOADER — warms cache before requests
# ═══════════════════════════════════════════════════════
def background_preload():
    """Pre-load hot sheets into cache every 5 seconds"""
    time.sleep(3)  # Wait for server to start
    while True:
        try:
            # Always keep orders fresh
            key = f"sheet:{ORDERS}"
            hit = cache_get(key)
            if hit is None:
                ws = get_cached_ws(ORDERS)
                if ws:
                    data = ws.get_all_records()
                    cache_set(key, data, CACHE_TTL["orders"])
            # Keep payments fresh
            key2 = f"sheet:{PAYMENTS}"
            if cache_get(key2) is None:
                ws2 = get_cached_ws(PAYMENTS)
                if ws2:
                    data2 = ws2.get_all_records()
                    cache_set(key2, data2, CACHE_TTL["payments"])
        except Exception as e:
            print(f"Preload error: {e}")
            bust_ws_cache()
        time.sleep(5)

# Start background preloader
_preload_thread = threading.Thread(target=background_preload, daemon=True)
_preload_thread.start()

# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════
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
    try: return ws.get_all_records()
    except: return []

# ═══════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════
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
                session["role"]     = "admin"
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

# ═══════════════════════════════════════════════════════
#  ADMIN — DASHBOARD
# ═══════════════════════════════════════════════════════
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin/dashboard.html")

@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    period   = request.args.get("period","today")
    category = request.args.get("category","all")
    offset   = int(request.args.get("offset", 0))

    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["stats"])
    now    = datetime.now()

    def clean_dt(dt):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "today":
        start = clean_dt(now + timedelta(days=offset))
        end   = start + timedelta(days=1)
    elif period == "week":
        s     = clean_dt(now - timedelta(days=now.weekday()))
        start = s + timedelta(weeks=offset)
        end   = start + timedelta(weeks=1)
    elif period == "month":
        s = clean_dt(now).replace(day=1)
        m = s.month + offset; y = s.year
        while m > 12: m -= 12; y += 1
        while m < 1:  m += 12; y -= 1
        start = s.replace(month=m, year=y)
        end   = start.replace(month=m+1) if m < 12 else start.replace(year=y+1, month=1)
    elif period == "year":
        start = clean_dt(now).replace(month=1, day=1, year=now.year + offset)
        end   = start.replace(year=start.year+1)
    else:
        start = clean_dt(now)
        end   = start + timedelta(days=1)

    filtered = []
    for o in orders:
        try:
            ds = str(o.get("Date","")).strip()
            if not ds: continue
            d = None
            for val, fmt in [(ds[:19], "%Y-%m-%d %H:%M:%S"), (ds[:10], "%Y-%m-%d")]:
                try: d = datetime.strptime(val, fmt); break
                except: continue
            if d and start.date() <= d.date() < end.date():
                if category == "all" or str(o.get("Items","")).lower().find(category.lower()) >= 0:
                    filtered.append(o)
        except: pass

    total_revenue = sum(safe_float(o.get("Final_Total",0)) for o in filtered)
    total_orders  = len(filtered)
    breakdown     = {"food":0,"shopping":0,"khudaar":0,"raashin":0}
    for o in filtered:
        items_str = str(o.get("Items","")).lower()
        for cat in breakdown:
            if cat in items_str:
                breakdown[cat] += safe_float(o.get("Final_Total",0))

    return jsonify({
        "total_revenue": total_revenue,
        "total_orders":  total_orders,
        "breakdown":     breakdown,
        "orders":        filtered,
        "period_label":  f"{start.strftime('%Y-%m-%d')} → {(end-timedelta(days=1)).strftime('%Y-%m-%d')}"
    })

# ═══════════════════════════════════════════════════════
#  NOTIFICATIONS / ORDERS
# ═══════════════════════════════════════════════════════
@app.route("/api/notifications")
@admin_required
def notifications():
    orders  = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    pending = [o for o in orders if str(o.get("Status","")).strip().lower() == "pending"]
    return jsonify({"count": len(pending), "orders": pending})

# Track processing to prevent double-click
_processing_orders = set()
_processing_lock   = threading.Lock()

@app.route("/api/order/accept", methods=["POST"])
@admin_required
def accept_order():
    data     = request.json
    order_id = data.get("order_id")
    with _processing_lock:
        if order_id in _processing_orders:
            return jsonify({"success": False, "message": "Already processing"})
        _processing_orders.add(order_id)
    try:
        ws      = get_cached_ws(ORDERS)
        records = ws.get_all_records()
        headers = ws.row_values(1)
        status_col = headers.index("Status") + 1
        for i, row in enumerate(records, start=2):
            if str(row.get("Order_ID","")) == str(order_id):
                if str(row.get("Status","")).lower() != "pending":
                    return jsonify({"success": False, "message": "Already accepted"})
                ws.update_cell(i, status_col, "Processed")
                cache_del_prefix("sheet:")
                bust_ws_cache()
                pws = get_cached_ws(PAYMENTS)
                pid = short_id()
                # Parse items to get clean category text
                items_raw = row.get("Items","")
                try:
                    items_list = json.loads(items_raw)
                    if isinstance(items_list, list):
                        category_text = " | ".join([
                            f"{i.get('name','?')} x{i.get('qty',1)}"
                            for i in items_list
                        ])
                    else:
                        category_text = items_raw
                except:
                    category_text = items_raw
                pws.append_row([pid, order_id, row.get("Final_Total",""),
                    category_text, now_str(),
                    datetime.now().strftime("%H:%M:%S"), "Paid"])
                return jsonify({"success": True})
        return jsonify({"success": False, "message": "Order not found"})
    finally:
        with _processing_lock:
            _processing_orders.discard(order_id)

@app.route("/api/order/decline", methods=["POST"])
@admin_required
def decline_order():
    data     = request.json
    order_id = data.get("order_id")
    with _processing_lock:
        if order_id in _processing_orders:
            return jsonify({"success": False, "message": "Already processing"})
        _processing_orders.add(order_id)
    try:
        ws      = get_cached_ws(ORDERS)
        records = ws.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("Order_ID","")) == str(order_id):
                ws.delete_rows(i)
                cache_del_prefix("sheet:")
                bust_ws_cache()
                return jsonify({"success": True})
        return jsonify({"success": False, "message": "Not found"})
    finally:
        with _processing_lock:
            _processing_orders.discard(order_id)

@app.route("/api/order/status", methods=["POST"])
@admin_required
def update_order_status():
    data     = request.json
    order_id = data.get("order_id")
    status   = data.get("status")
    ws       = get_cached_ws(ORDERS)
    records  = ws.get_all_records()
    headers  = ws.row_values(1)
    status_col = headers.index("Status") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            ws.update_cell(i, status_col, status)
            cache_del_prefix("sheet:")
            bust_ws_cache()
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/order/create", methods=["POST"])
@admin_required
def create_order():
    data       = request.json
    order_id   = "ORD-" + short_id()
    items_json = json.dumps(data.get("items", []))
    total      = safe_float(data.get("total", 0))
    discount   = safe_float(data.get("discount", 0))
    final      = total - (total * discount / 100)
    ws = get_cached_ws(ORDERS)
    cache_del_prefix("sheet:")
    bust_ws_cache()
    ws.append_row([order_id, data.get("customer_name","Admin Order"),
        data.get("phone",""), data.get("city",""), data.get("location",""),
        data.get("district",""), data.get("sender_name",""),
        items_json, total, discount, round(final,2), "Pending",
        data.get("payment_method","Cash"), now_str(),
        datetime.now().strftime("%H:%M:%S")])
    return jsonify({"success": True, "order_id": order_id})

@app.route("/admin/live-orders")
@admin_required
def admin_live_orders():
    return render_template("admin/live_order.html")

@app.route("/api/delivered-orders")
@admin_required
def delivered_orders():
    orders    = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    delivered = [o for o in orders if str(o.get("Status","")).lower() not in ("pending","")]
    return jsonify({"orders": delivered})

@app.route("/api/all-orders")
@admin_required
def all_orders():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    return jsonify({"orders": orders})

# ═══════════════════════════════════════════════════════
#  ADMIN — MENU
# ═══════════════════════════════════════════════════════
@app.route("/admin/menu")
@admin_required
def admin_menu():
    return render_template("admin/menu.html")

CATEGORY_SHEETS = {
    "food":     FOOD_MENU,
    "shopping": SHOPPING_MENU,
    "khudaar":  KHUDAAR_MENU,
    "raashin":  RAASHIN_MENU
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
        data       = request.json
        category   = data.get("category","").lower()
        sheet_name = CATEGORY_SHEETS.get(category)
        if not sheet_name:
            return jsonify({"success": False, "message": f"Category not found: {category}"})
        ws = get_cached_ws(sheet_name)
        if not ws:
            return jsonify({"success": False, "message": "Sheet connection failed"})
        records   = ws.get_all_records()
        new_id    = len(records) + 1
        image_url = data.get("image_url","")
        if len(str(image_url)) > 45000:
            image_url = str(image_url)[:45000]
        ws.append_row([new_id, data.get("name",""),
            float(data.get("price",0)), image_url,
            category.capitalize(), today_str()])
        cache_del(f"sheet:{sheet_name}")
        bust_ws_cache()
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        print(f"add_menu_item error: {e}")
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/menu/delete", methods=["POST"])
@admin_required
def delete_menu_item():
    data       = request.json
    category   = data.get("category","").lower()
    item_id    = str(data.get("id",""))
    sheet_name = CATEGORY_SHEETS.get(category)
    if not sheet_name:
        return jsonify({"success": False})
    ws      = get_cached_ws(sheet_name)
    records = ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if str(row.get("ID","")) == item_id:
            ws.delete_rows(i)
            cache_del(f"sheet:{sheet_name}")
            bust_ws_cache()
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "Item not found"})

@app.route("/api/menu/upload-image-local", methods=["POST"])
@admin_required
def upload_image_local():
    import uuid as _uuid
    file = request.files.get("image")
    if not file:
        return jsonify({"success": False, "message": "No image"})
    try:
        ext  = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else "jpg"
        if ext not in ["jpg","jpeg","png","gif","webp"]: ext = "jpg"
        fname    = "menu_" + str(_uuid.uuid4())[:8] + "." + ext
        save_dir = os.path.join(BASE_DIR, "static", "images")
        os.makedirs(save_dir, exist_ok=True)
        file.save(os.path.join(save_dir, fname))
        return jsonify({"success": True, "url": f"/static/images/{fname}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# ═══════════════════════════════════════════════════════
#  ADMIN — PAYMENTS
# ═══════════════════════════════════════════════════════
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

    def clean_dt(dt):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "today":
        start = clean_dt(now + timedelta(days=offset))
        end   = start + timedelta(days=1)
    elif period == "week":
        s     = clean_dt(now - timedelta(days=now.weekday()))
        start = s + timedelta(weeks=offset)
        end   = start + timedelta(weeks=1)
    elif period == "month":
        s = clean_dt(now).replace(day=1)
        m = s.month + offset; y = s.year
        while m > 12: m -= 12; y += 1
        while m < 1:  m += 12; y -= 1
        start = s.replace(month=m, year=y)
        end   = start.replace(month=m+1) if m < 12 else start.replace(year=y+1, month=1)
    elif period == "year":
        start = clean_dt(now).replace(month=1, day=1, year=now.year + offset)
        end   = start.replace(year=start.year+1)
    else:
        start = clean_dt(now)
        end   = start + timedelta(days=1)

    filtered = []
    for p in payments:
        try:
            ds = str(p.get("Date","")).strip()
            if not ds: continue
            d = None
            for val, fmt in [(ds[:19], "%Y-%m-%d %H:%M:%S"), (ds[:10], "%Y-%m-%d")]:
                try: d = datetime.strptime(val, fmt); break
                except: continue
            if d and start.date() <= d.date() < end.date():
                filtered.append(p)
        except Exception as pe:
            print(f"Payment date error: {pe}")
            continue

    total = sum(safe_float(p.get("Amount",0)) for p in filtered)
    return jsonify({"payments": filtered, "total": total})

@app.route("/api/payments/edit", methods=["POST"])
@admin_required
def edit_payment():
    data       = request.json
    payment_id = str(data.get("payment_id",""))
    new_amount = data.get("amount")
    ws         = get_cached_ws(PAYMENTS)
    records    = ws.get_all_records()
    headers    = ws.row_values(1)
    amount_col = headers.index("Amount") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Payment_ID","")) == payment_id:
            ws.update_cell(i, amount_col, new_amount)
            cache_del(f"sheet:{PAYMENTS}")
            bust_ws_cache()
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/payments/delete", methods=["POST"])
@admin_required
def delete_payment():
    try:
        data       = request.json
        payment_id = str(data.get("payment_id",""))
        order_id   = str(data.get("order_id",""))
        pws        = get_cached_ws(PAYMENTS)
        records    = pws.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("Payment_ID","")) == payment_id:
                pws.delete_rows(i)
                break
        if order_id:
            ows      = get_cached_ws(ORDERS)
            orecords = ows.get_all_records()
            headers  = ows.row_values(1)
            try:
                status_col = headers.index("Status") + 1
                for i, row in enumerate(orecords, start=2):
                    if str(row.get("Order_ID","")) == order_id:
                        ows.update_cell(i, status_col, "Rejected")
                        break
            except: pass
        cache_del_prefix("sheet:")
        bust_ws_cache()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# ═══════════════════════════════════════════════════════
#  ADMIN — ABOUT
# ═══════════════════════════════════════════════════════
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
    data    = request.json
    content = data.get("content","")
    ws      = get_cached_ws(ABOUT)
    records = ws.get_all_records()
    if records:
        ws.update_cell(2, 1, content)
    else:
        ws.append_row([content])
    cache_del(f"sheet:{ABOUT}")
    bust_ws_cache()
    return jsonify({"success": True})

@app.route("/api/payment/items")
@admin_required
def get_payment_items():
    """Get items for a specific payment to edit"""
    payment_id = request.args.get("payment_id","")
    # Get from Orders sheet using Order_ID
    payments = cached_sheet(PAYMENTS, ttl=5)
    for p in payments:
        if str(p.get("Payment_ID","")) == payment_id:
            order_id = str(p.get("Order_ID",""))
            # Get items from Orders sheet
            orders = cached_sheet(ORDERS, ttl=5)
            for o in orders:
                if str(o.get("Order_ID","")) == order_id:
                    items_raw = str(o.get("Items",""))
                    try:
                        items = json.loads(items_raw)
                        if not isinstance(items, list): items = [items]
                        return jsonify({"items": items})
                    except:
                        # Plain text — convert to item
                        return jsonify({"items": [{"name": items_raw, "qty": 1, "price": 0, "category": "food"}]})
            break
    return jsonify({"items": []})

@app.route("/api/payment/items/update", methods=["POST"])
@admin_required
def update_payment_items():
    """Update items in both Payments and Orders sheets"""
    try:
        data       = request.json
        payment_id = data.get("payment_id","")
        order_id   = data.get("order_id","")
        items      = data.get("items",[])
        total      = data.get("total",0)

        # Build clean category text for Payments sheet
        cat_text = " | ".join([f"{i.get('name','?')} x{i.get('qty',1)}" for i in items])
        items_json = json.dumps(items)

        # Update Payments sheet — Category + Amount
        pws      = get_cached_ws(PAYMENTS)
        precords = pws.get_all_records()
        pheaders = pws.row_values(1)
        cat_col  = pheaders.index("Category") + 1
        amt_col  = pheaders.index("Amount")   + 1
        for i, row in enumerate(precords, start=2):
            if str(row.get("Payment_ID","")) == payment_id:
                pws.update_cell(i, cat_col, cat_text)
                pws.update_cell(i, amt_col, float(total))
                break

        # Update Orders sheet — Items + totals
        if order_id:
            ows      = get_cached_ws(ORDERS)
            orecords = ows.get_all_records()
            oheaders = ows.row_values(1)
            items_col = oheaders.index("Items")       + 1
            total_col = oheaders.index("Total")       + 1
            final_col = oheaders.index("Final_Total") + 1
            for i, row in enumerate(orecords, start=2):
                if str(row.get("Order_ID","")) == order_id:
                    ows.update_cell(i, items_col, items_json)
                    ows.update_cell(i, total_col, float(total))
                    ows.update_cell(i, final_col, float(total))
                    break

        cache_del_prefix("sheet:")
        bust_ws_cache()
        return jsonify({"success": True})
    except Exception as e:
        print(f"update_payment_items error: {e}")
        return jsonify({"success": False, "message": str(e)})

# ═══════════════════════════════════════════════════════
#  CUSTOMER
# ═══════════════════════════════════════════════════════
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

# ── Duplicate order prevention ──
_recent_orders      = {}
_recent_orders_lock = threading.Lock()

@app.route("/api/customer/order", methods=["POST"])
@customer_required
def customer_place_order():
    data       = request.json
    phone      = data.get("phone","")
    items_json = json.dumps(data.get("items",[]))
    total      = safe_float(data.get("total",0))

    dup_key = f"{phone}_{total}_{items_json[:50]}"
    with _recent_orders_lock:
        now_ts = time.time()
        if dup_key in _recent_orders:
            if now_ts - _recent_orders[dup_key] < 5:
                return jsonify({"success": False, "message": "Duplicate order"})
        _recent_orders[dup_key] = now_ts

    order_id = "ORD-" + short_id()
    ws = get_cached_ws(ORDERS)
    cache_del_prefix("sheet:")
    bust_ws_cache()
    ws.append_row([order_id, data.get("full_name",""), phone,
        data.get("city",""), data.get("location",""),
        data.get("district",""), data.get("sender_name",""),
        items_json, total, 0, total, "Pending",
        data.get("payment_method",""), now_str(),
        datetime.now().strftime("%H:%M:%S")])
    return jsonify({"success": True, "order_id": order_id})

@app.route("/api/customer/orders")
@customer_required
def customer_orders():
    orders = cached_sheet(ORDERS, ttl=CACHE_TTL["orders"])
    return jsonify({"orders": orders})

@app.route("/api/customer/received", methods=["POST"])
@customer_required
def customer_received():
    data     = request.json
    order_id = data.get("order_id")
    ws       = get_cached_ws(ORDERS)
    records  = ws.get_all_records()
    headers  = ws.row_values(1)
    status_col = headers.index("Status") + 1
    for i, row in enumerate(records, start=2):
        if str(row.get("Order_ID","")) == str(order_id):
            ws.update_cell(i, status_col, "Delivery Confirmed")
            cache_del_prefix("sheet:")
            bust_ws_cache()
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/message/send", methods=["POST"])
@customer_required
def send_message():
    data    = request.json
    msg     = data.get("message","")
    ws      = get_cached_ws(MESSAGES)
    records = ws.get_all_records()
    mid     = len(records) + 1
    ws.append_row([mid, msg, today_str(), datetime.now().strftime("%H:%M:%S")])
    cache_del(f"sheet:{MESSAGES}")
    return jsonify({"success": True})

# ═══════════════════════════════════════════════════════
#  PWA
# ═══════════════════════════════════════════════════════
@app.route("/sw.js")
def service_worker():
    from flask import send_from_directory
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

# ═══════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)

import os
import json
import sqlite3
import hmac
import secrets
import uuid
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask import send_from_directory
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import smtplib
import ssl
import urllib.request
import urllib.error
import threading
from email.message import EmailMessage

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DB_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "orders.db"))
CATALOG_PATH = Path(os.environ.get("CATALOG_PATH", BASE_DIR / "products.json"))
UPLOAD_FOLDER = Path(os.environ.get("UPLOAD_FOLDER", BASE_DIR / "static" / "uploads"))
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    )
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "")
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

# Настройки уведомлений
# Настройки уведомлений
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_IDS = [
    chat_id.strip()
    for chat_id in os.getenv("TELEGRAM_CHAT_IDS", "").replace(";", ",").split(",")
    if chat_id.strip()
]

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()

NOTIFY_EMAILS = [
    address.strip()
    for address in os.getenv("NOTIFY_EMAILS", "").replace(";", ",").split(",")
    if address.strip()
]

if not app.secret_key:
    # Development convenience only. Set SECRET_KEY in production.
    app.secret_key = secrets.token_hex(32)
    print("WARNING: SECRET_KEY is temporary. Set SECRET_KEY in the deployment environment.")
if not ADMIN_PASSWORD_HASH:
    print("WARNING: ADMIN_PASSWORD_HASH is not set. Create it with: python -c \"from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))\"")

STATUSES = {
    "new": "Новый",
    "processing": "В обработке",
    "ready": "Готов к выдаче",
    "delivering": "Доставляется",
    "completed": "Выполнен",
    "cancelled": "Отменён",
}

def format_minsk_time(value):
    if not value:
        return ""

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(ZoneInfo("Europe/Minsk")).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return value

def send_telegram_notification(message):
    """Отправляет уведомление всем получателям из TELEGRAM_CHAT_IDS."""

    if not TELEGRAM_BOT_TOKEN:
        app.logger.warning("Не задан токен Telegram-бота.")
        return False

    if not TELEGRAM_CHAT_IDS:
        app.logger.warning("Список получателей Telegram пуст.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    sent_count = 0

    for chat_id in TELEGRAM_CHAT_IDS:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))

            if result.get("ok"):
                sent_count += 1
            else:
                app.logger.error(
                    "Telegram не подтвердил отправку получателю %s.",
                    chat_id
                )

        except Exception:
            app.logger.exception(
                "Ошибка отправки Telegram-уведомления получателю %s.",
                chat_id
            )

    app.logger.info(
        "Telegram: уведомление отправлено %s из %s получателей.",
        sent_count,
        len(TELEGRAM_CHAT_IDS)
    )

    return sent_count > 0


def send_email_notification(subject, message):
    """Отправляет письмо всем активным адресатам из NOTIFY_EMAILS."""
    if not SMTP_USER or not SMTP_PASSWORD or not NOTIFY_EMAILS:
        app.logger.warning("Почта не настроена. Уведомление не отправлено.")
        return False

    context = ssl.create_default_context()
    sent_count = 0

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)

            for recipient in NOTIFY_EMAILS:
                try:
                    email = EmailMessage()
                    email["Subject"] = subject
                    email["From"] = SMTP_FROM
                    email["To"] = recipient
                    email.set_content(message)

                    refused = server.send_message(email)

                    if not refused:
                        sent_count += 1
                    else:
                        app.logger.warning(
                            "Почтовый сервер отклонил одного из получателей."
                        )

                except Exception:
                    app.logger.exception(
                        "Не удалось отправить письмо одному из получателей."
                    )

        return sent_count > 0

    except Exception:
        app.logger.exception("Ошибка подключения к SMTP-серверу Gmail.")
        return False

def notify_admins_background(subject, message):
    """Отправляет уведомления в фоновом потоке."""
    try:
        notify_admins(subject, message)
    except Exception:
        app.logger.exception(
            "Ошибка фоновой отправки уведомлений администраторам."
        )

def notify_admins(subject, message):
    """Отправляет уведомления по всем настроенным каналам."""
    send_telegram_notification(message)
    send_email_notification(subject, message)

def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            delivery_method TEXT NOT NULL DEFAULT 'delivery',
            comment TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            total REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catering_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            request_type TEXT NOT NULL DEFAULT 'Фуршет/банкет',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0)
        );
        CREATE TABLE IF NOT EXISTS admin_login_attempts (
            ip_address TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            window_started REAL NOT NULL,
            locked_until REAL NOT NULL DEFAULT 0
        );
        """)
        db.commit()

def load_products():
    try:
        with CATALOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        app.logger.exception("Cannot read product catalog")
        return []
    products = []
    for item in data:
        try:
            product_id = str(item["id"]).strip()
            name = str(item["name"]).strip()
            price = round(float(item["price"]), 2)
            category = str(item.get("category", "Другое")).strip()
            unit = str(item.get("unit", "шт.")).strip()
            description = str(item.get("description", "")).strip()
            image = str(item.get("image", "")).strip()
            active = bool(item.get("active", True))
            if product_id and name and price >= 0 and active:
                products.append({
                    "id": product_id, "name": name, "price": price,
                    "category": category, "unit": unit,
                    "description": description, "image": image
                })
        except (KeyError, TypeError, ValueError):
            app.logger.warning("Skipping invalid product row: %r", item)
    return products

def read_catalog_all():
    try:
        with CATALOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        app.logger.exception("Cannot read product catalog for editing")
        return []

def write_catalog(data):
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CATALOG_PATH.with_suffix(CATALOG_PATH.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp_path.replace(CATALOG_PATH)

def valid_admin_csrf():
    return hmac.compare_digest(session.get("admin_csrf", ""), request.form.get("csrf_token", ""))



def save_product_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    # Берём исходное имя файла, переданное браузером.
    original_filename = file_storage.filename.strip()

    # Убираем возможный путь к файлу.
    original_filename = original_filename.replace("\\", "/").split("/")[-1]

    # Извлекаем расширение до обработки имени.
    if "." not in original_filename:
        raise ValueError(
            f"В имени файла нет расширения: {original_filename!r}"
        )

    ext = original_filename.rsplit(".", 1)[-1].lower()

    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(
            f"Неподдерживаемый формат: {ext!r}. "
            "Допустимые форматы: JPG, JPEG, PNG, WEBP, GIF."
        )

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(UPLOAD_FOLDER / filename)

    app.logger.info(
        "Фото сохранено: исходное имя=%r, итоговое имя=%r",
        original_filename, filename
    )

    return f"uploads/{filename}"



def remove_product_image(image_path):
    if not image_path or not image_path.startswith("uploads/"):
        return
    candidate = (BASE_DIR / "static" / image_path).resolve()
    uploads_root = UPLOAD_FOLDER.resolve()
    try:
        candidate.relative_to(uploads_root)
    except ValueError:
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        app.logger.warning("Could not remove product image %s", candidate)

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response

@app.route("/delivery")
def delivery():
    return render_template("delivery.html")

@app.route("/")
def index():
    products = load_products()
    categories = sorted({p["category"] for p in products})

    gallery_dir = os.path.join(
        app.static_folder, "images", "gallery"
    )

    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    gallery_photos = []

    if os.path.isdir(gallery_dir):
        gallery_photos = sorted(
            filename
            for filename in os.listdir(gallery_dir)
            if os.path.splitext(filename)[1].lower() in allowed_extensions
            and os.path.isfile(os.path.join(gallery_dir, filename))
        )

    return render_template(
        "index.html",
        products=products,
        categories=categories,
        gallery_photos=gallery_photos
    )


@app.post("/order")
def create_order():
    order_started = time.perf_counter()
    # Basic anti-CSRF token and honeypot. For a larger deployment use Flask-WTF/CSRF.
    token = session.get("order_csrf")
    submitted_token = request.form.get("csrf_token", "")
    if not token or not hmac.compare_digest(token, submitted_token):
        abort(400, "Форма устарела. Обновите страницу и попробуйте ещё раз.")
    if request.form.get("website", "").strip():
        abort(400)

    name = request.form.get("customer_name", "").strip()[:40]
    phone = request.form.get("phone", "").strip()[:40]
    address = request.form.get("address", "").strip()[:100]
    delivery_method = request.form.get("delivery_method", "delivery")
    comment = request.form.get("comment", "").strip()[:500]
    if delivery_method not in {"delivery", "pickup"}:
        delivery_method = "delivery"
    if not phone:
        flash("Укажите номер телефона.", "error")
        return redirect(url_for("index") + "#checkout")
    if delivery_method == "delivery" and not address:
        flash("Укажите адрес доставки.", "error")
        return redirect(url_for("index") + "#checkout")
    products_by_id = {p["id"]: p for p in load_products()}
    items = []
    total = 0.0
    for key, value in request.form.items():
        if not key.startswith("qty_"):
            continue
        product_id = key[4:]
        if product_id not in products_by_id:
            continue
        try:
            quantity = int(value)
        except ValueError:
            continue
        if quantity <= 0:
            continue
        if quantity > 99:
            flash("Количество одного товара не может превышать 99.", "error")
            return redirect(url_for("index"))
        product = products_by_id[product_id]
        line_total = round(product["price"] * quantity, 2)
        total += line_total
        items.append((product["id"], product["name"], product["price"], quantity))

    if not items:
        flash("Добавьте хотя бы один товар в заказ.", "error")
        return redirect(url_for("index"))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_started = time.perf_counter()
    with connect_db() as db:
        cursor = db.execute(
            """INSERT INTO orders
            (customer_name, phone, address, delivery_method, comment, status, total, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?)""",
            (name, phone, address, delivery_method, comment, round(total, 2), now, now)
        )
        order_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (?, ?, ?, ?, ?)",
            [(order_id, *item) for item in items]
        )
        db.commit()
    db_seconds = time.perf_counter() - db_started

    # Формируем уведомление о новом заказе
    delivery_text = (
        "Доставка" if delivery_method == "delivery" else "Самовывоз"
    )

    message_lines = [
        f"НОВЫЙ ЗАКАЗ №{order_id}",
        "",
        f"Покупатель: {name or 'Не указано'}",
        f"Телефон: {phone}",
        f"Получение: {delivery_text}",
    ]

    if address:
        message_lines.append(f"Адрес: {address}")

    message_lines.extend(["", "Состав заказа:"])

    for product_id, product_name, price, quantity in items:
        line_total = price * quantity
        message_lines.append(
            f"{product_name} — {quantity} × {price:.2f} = {line_total:.2f}"
        )

    message_lines.extend([
        "",
        f"ИТОГО: {total:.2f}",
    ])

    if comment:
        message_lines.extend(["", f"Комментарий: {comment}"])

    message = "\n".join(message_lines)

    notification_thread = threading.Thread(
        target=notify_admins_background,
        args=(
            f"Новый заказ №{order_id} — StarFurshet",
            message
        ),
        daemon=True
    )
    notification_thread.start()

    session.pop("order_csrf", None)

    return render_template(
        "order_success.html",
        order_id=order_id,
        total=round(total, 2)
    )

    
         

@app.post("/catering-request")
def create_catering_request():
    token = session.get("catering_csrf")
    submitted_token = request.form.get("csrf_token", "")

    if not token or not hmac.compare_digest(token, submitted_token):
        abort(400, "Форма устарела. Обновите страницу и попробуйте ещё раз.")

    if request.form.get("website", "").strip():
        abort(400)

    phone = request.form.get("phone", "").strip()[:40]
    digits = "".join(char for char in phone if char.isdigit())

    allowed_chars = set("+0123456789 ()-")
    if (
        not phone
        or len(digits) < 7
        or len(digits) > 15
        or any(char not in allowed_chars for char in phone)
    ):
        flash("Укажите корректный номер телефона.", "error")
        return redirect(url_for("index") + "#about")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        with connect_db() as db:
            cursor = db.execute(
                """INSERT INTO catering_requests
                (phone, request_type, status, created_at)
                VALUES (?, ?, 'new', ?)""",
                (phone, "Фуршет/банкет", now)
            )
            catering_id = cursor.lastrowid
            db.commit()
    except Exception:
        app.logger.exception("Не удалось сохранить заявку на мероприятие")
        flash("Не удалось отправить заявку. Попробуйте ещё раз.", "error")
        return redirect(url_for("index") + "#about")
    message = (
        f"НОВАЯ ЗАЯВКА НА МЕРОПРИЯТИЕ №{catering_id}\n\n"
        f"Тип: Фуршет/банкет\n"
        f"Телефон: {phone}"
    )

    notification_thread = threading.Thread(
        target=notify_admins_background,
        args=(
            f"Новая заявка на мероприятие №{catering_id} — StarFurshet",
            message
        ),
        daemon=True
    )
    notification_thread.start()
    session.pop("catering_csrf", None)
    flash(
        "Заявка отправлена. Мы свяжемся с вами в ближайшее время, "
        "чтобы уточнить детали мероприятия.",
        "success"
    )
    return redirect(url_for("index") + "#about")

@app.before_request
def prepare_order_csrf():
    if request.endpoint == "index":
        if "order_csrf" not in session:
            session["order_csrf"] = secrets.token_urlsafe(24)

        if "catering_csrf" not in session:
            session["catering_csrf"] = secrets.token_urlsafe(24)

def get_admin_client_ip():
    return (
        request.headers.get("X-Real-IP", "").strip()
        or request.remote_addr
        or "unknown"
    )


def is_admin_login_locked(ip_address):
    now = time.time()

    with connect_db() as db:
        row = db.execute(
            "SELECT locked_until FROM admin_login_attempts WHERE ip_address = ?",
            (ip_address,)
        ).fetchone()

        if row and row["locked_until"] > now:
            return True

        if row and row["locked_until"] > 0:
            db.execute(
                "DELETE FROM admin_login_attempts WHERE ip_address = ?",
                (ip_address,)
            )
            db.commit()

    return False


def record_failed_admin_login(ip_address):
    now = time.time()

    with connect_db() as db:
        row = db.execute(
            """
            SELECT attempts, window_started
            FROM admin_login_attempts
            WHERE ip_address = ?
            """,
            (ip_address,)
        ).fetchone()

        if not row or now - row["window_started"] > 900:
            attempts = 1
            window_started = now
        else:
            attempts = row["attempts"] + 1
            window_started = row["window_started"]

        locked_until = now + 900 if attempts >= 5 else 0

        db.execute(
            """
            INSERT OR REPLACE INTO admin_login_attempts
            (ip_address, attempts, window_started, locked_until)
            VALUES (?, ?, ?, ?)
            """,
            (ip_address, attempts, window_started, locked_until)
        )
        db.commit()


def clear_admin_login_attempts(ip_address):
    with connect_db() as db:
        db.execute(
            "DELETE FROM admin_login_attempts WHERE ip_address = ?",
            (ip_address,)
        )
        db.commit()

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_authenticated"):
        return redirect(url_for("admin_orders"))

    if request.method == "POST":
        ip_address = get_admin_client_ip()

        if is_admin_login_locked(ip_address):
            flash(
                "Слишком много неудачных попыток. "
                "Попробуйте войти через 15 минут.",
                "error"
            )
            return render_template("admin_login.html"), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if (
            ADMIN_PASSWORD_HASH
            and hmac.compare_digest(
                username.encode("utf-8"),
                ADMIN_USERNAME.encode("utf-8")
            )
            and check_password_hash(ADMIN_PASSWORD_HASH, password)
        ):
            clear_admin_login_attempts(ip_address)

            session.clear()
            session["admin_authenticated"] = True
            session["admin_csrf"] = secrets.token_urlsafe(24)

            return redirect(url_for("admin_orders"))

        record_failed_admin_login(ip_address)
        flash("Неверный логин или пароль.", "error")

    return render_template("admin_login.html")

@app.route("/admin/menu")
@admin_required
def admin_menu():
    products = read_catalog_all()
    return render_template("admin_menu.html", products=products)

@app.route("/admin/menu/add", methods=["GET", "POST"])
@admin_required
def admin_product_add():
    if request.method == "POST":
        if not valid_admin_csrf():
            abort(400, "Недействительный токен формы")
        try:
            name = request.form.get("name", "").strip()[:160]
            price = round(float(request.form.get("price", "")), 2)
            description = request.form.get("description", "").strip()[:1000]
            category = request.form.get("category", "Другое").strip()[:100]
            unit = request.form.get("unit", "шт.").strip()[:30]
            if not name or price < 0 or not category or not unit:
                raise ValueError("Заполните название, цену, категорию и единицу измерения.")
            image = save_product_image(request.files.get("image"))
        except (ValueError, TypeError) as exc:
            flash(str(exc) or "Проверьте введённые данные.", "error")
            return render_template("admin_product_form.html", product=None, page_title="Добавить товар"), 400
        products = read_catalog_all()
        products.append({"id": uuid.uuid4().hex[:12], "name": name, "price": price,
                         "category": category, "unit": unit, "description": description,
                         "image": image})
        write_catalog(products)
        flash(f"Товар «{name}» добавлен.", "success")
        return redirect(url_for("admin_menu"))
    return render_template("admin_product_form.html", product=None, page_title="Добавить товар")

@app.route("/admin/menu/<product_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(product_id):
    products = read_catalog_all()
    product = next((item for item in products if str(item.get("id")) == product_id), None)
    if product is None:
        abort(404)
    if request.method == "POST":
        if not valid_admin_csrf():
            abort(400, "Недействительный токен формы")
        try:
            name = request.form.get("name", "").strip()[:160]
            price = round(float(request.form.get("price", "")), 2)
            description = request.form.get("description", "").strip()[:1000]
            category = request.form.get("category", "Другое").strip()[:100]
            unit = request.form.get("unit", "шт.").strip()[:30]
            if not name or price < 0 or not category or not unit:
                raise ValueError("Заполните название, цену, категорию и единицу измерения.")
            old_image = str(product.get("image", ""))
            new_image = save_product_image(request.files.get("image"))
        except (ValueError, TypeError) as exc:
            flash(str(exc) or "Проверьте введённые данные.", "error")
            return render_template("admin_product_form.html", product=product, page_title="Редактировать товар"), 400
        if new_image:
            product["image"] = new_image
            remove_product_image(old_image)
        elif request.form.get("remove_image") == "1":
            product["image"] = ""
            remove_product_image(old_image)
        product.update({"name": name, "price": price, "description": description,
                        "category": category, "unit": unit})
        write_catalog(products)
        flash(f"Товар «{name}» сохранён.", "success")
        return redirect(url_for("admin_menu"))
    return render_template("admin_product_form.html", product=product, page_title="Редактировать товар")

@app.post("/admin/menu/<product_id>/delete")
@admin_required
def admin_product_delete(product_id):
    if not valid_admin_csrf():
        abort(400, "Недействительный токен формы")
    products = read_catalog_all()
    product = next((item for item in products if str(item.get("id")) == product_id), None)
    if product is None:
        abort(404)
    remaining = [item for item in products if str(item.get("id")) != product_id]
    write_catalog(remaining)
    remove_product_image(str(product.get("image", "")))
    flash(f"Товар «{product.get('name', product_id)}» удалён из каталога. История заказов сохранена.", "success")
    return redirect(url_for("admin_menu"))

@app.post("/admin/logout")
@admin_required
def admin_logout():
    if not hmac.compare_digest(session.get("admin_csrf", ""), request.form.get("csrf_token", "")):
        abort(400)
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_orders():
    with connect_db() as db:
        rows = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 300").fetchall()
        catering_rows = db.execute("SELECT * FROM catering_requests ORDER BY id DESC LIMIT 300").fetchall()
        orders = []
        for row in rows:
            order = dict(row)
            order["created_at_minsk"] = format_minsk_time(order["created_at"])
            order["status_label"] = STATUSES.get(order["status"], order["status"])
            order["items"] = [dict(item) for item in db.execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order["id"],)
            ).fetchall()]
            orders.append(order)

        # Добавляем заявки на фуршет и банкет в общий список
        for row in catering_rows:
            order = dict(row)
            order["is_catering"] = True
            order["created_at_minsk"] = format_minsk_time(order["created_at"])
            order["status_label"] = STATUSES.get(
                order["status"], order["status"]
            )
            orders.append(order)

        # Сначала самые новые записи
        orders.sort(key=lambda order: order["created_at"], reverse=True)

        latest_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM orders"
        ).fetchone()[0]
        latest_catering_id = db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM catering_requests"
        ).fetchone()[0]

    return render_template(
        "admin_orders.html",
        orders=orders,
        statuses=STATUSES,
        latest_catering_id=latest_catering_id,
        latest_id=latest_id
    )

@app.post("/admin/order/<int:order_id>/status")
@admin_required
def update_status(order_id):
    if not hmac.compare_digest(session.get("admin_csrf", ""), request.form.get("csrf_token", "")):
        abort(400)
    status = request.form.get("status", "")
    if status not in STATUSES:
        abort(400, "Недопустимый статус")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as db:
        cursor = db.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, now, order_id))
        db.commit()
        if cursor.rowcount == 0:
            abort(404)
    flash(f"Статус заказа №{order_id} обновлён.", "success")
    return redirect(url_for("admin_orders") + f"#order-{order_id}")


@app.get("/admin/api/updates")
@admin_required
def admin_updates():
    try:
        after_id = max(0, int(request.args.get("after_id", "0")))
        after_catering_id = max(
            0, int(request.args.get("after_catering_id", "0"))
        )
    except ValueError:
        after_id = 0
        after_catering_id = 0

    with connect_db() as db:
        rows = db.execute(
            """
            SELECT id, customer_name, total, created_at
            FROM orders
            WHERE id > ?
            ORDER BY id ASC
            LIMIT 100
            """,
            (after_id,)
        ).fetchall()

        catering_rows = db.execute(
            """
            SELECT id, phone, request_type, created_at
            FROM catering_requests
            WHERE id > ?
            ORDER BY id ASC
            LIMIT 100
            """,
            (after_catering_id,)
        ).fetchall()

    return jsonify({
        "orders": [dict(row) for row in rows],
        "catering_requests": [dict(row) for row in catering_rows]
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})

init_db()

@app.errorhandler(413)
def upload_too_large(error):
    return "Файл слишком большой. Максимальный размер фото — 5 МБ.", 413

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)


@app.get("/yandex_c414488b9d92b314.html")
def yandex_verification():
    return send_from_directory(
        app.root_path,
        "yandex_c414488b9d92b314.html"
    )

@app.get("/robots.txt")
def robots_txt():
    return send_from_directory(
        app.root_path,
        "robots.txt",
        mimetype="text/plain"
    )

@app.get("/sitemap.xml")
def sitemap_xml():
    return send_from_directory(
        app.root_path,
        "sitemap.xml",
        mimetype="application/xml"
    )

@app.get("/googlea90419548260e95d.html")
def google_verification():
    return send_from_directory(
        app.root_path,
        "googlea90419548260e95d.html"
    )
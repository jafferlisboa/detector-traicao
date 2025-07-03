from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, io
import requests
import qrcode

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'  # Mude para algo seguro

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

DB = 'users.db'

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            username TEXT UNIQUE NOT NULL,
                            password TEXT NOT NULL)''')
init_db()

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(*row) if row else None

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with sqlite3.connect(DB) as conn:
            row = conn.execute("SELECT id, username, password FROM users WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row[2], password):
            user = User(*row)
            login_user(user)
            return redirect(url_for('painel'))
        return render_template('index.html', erro="Login inválido.")
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', erro="Usuário já existe.")
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/painel')
@login_required
def painel():
    user_id = current_user.username
    qr_code = None
    try:
        # Troque o endereço para o IP público do seu Node.js se necessário!
        response = requests.post(
            "http://localhost:3000/start-session",
            json={"userId": user_id},
            timeout=15
        )
        response.raise_for_status()
        qr_code = response.json().get("qrCode")
    except Exception as e:
        print(f"Erro ao requisitar QR: {e}")

    return render_template("painel.html", session_id=user_id, qr_code=qr_code)


# --- ROTAS ANTIGAS DO QR LOCAL (PODEM SER REMOVIDAS SE QUISER) ---
@app.route('/qr/<session_id>')
@login_required
def mostrar_qr(session_id):
    if current_user.username != session_id:
        abort(403)
    path = f"./qrcodes/{session_id}.txt"
    if not os.path.exists(path):
        return f"QR code ainda não disponível para {session_id}."
    with open(path, "r") as f:
        qr_data = f.read()
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

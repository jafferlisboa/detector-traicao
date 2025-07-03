from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2, os, qrcode, io

app = Flask(__name__)
app.secret_key = '9df_g6cxovh433u4yuidlooo0x126_735dsnvfksscc'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

# ---- Dados de conexão do Render ----
DB_CONFIG = {
    'dbname': 'condominio_db_9tut',
    'user': 'condominio_user',
    'password': 'eOO34utsZTeeF7oE9uJE8D7OcgiQSfNC',
    'host': 'dpg-d1jbjtje5dus73c2qe0g-a.oregon-postgres.render.com',
    'port': '5432'
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

# ---- Criação da tabela se não existir ----
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
init_db()

@login_manager.user_loader
def load_user(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, password FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return User(*row) if row else None

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        cur.close()
        conn.close()
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
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
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
    return render_template("painel.html", session_id=current_user.username)

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

if __name__ == "__main__":
    app.run(debug=True)

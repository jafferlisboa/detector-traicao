from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import os

import openai # ou sua biblioteca IA favorita

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

# Configurações do Postgres
DB_HOST = "dpg-d1jbjtje5dus73c2qe0g-a.oregon-postgres.render.com"
DB_NAME = "condominio_db_9tut"
DB_USER = "condominio_user"
DB_PASS = "eOO34utsZTeeF7oE9uJE8D7OcgiQSfNC"
DB_PORT = 5432

def get_db():
    return psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        port=DB_PORT
    )

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, password FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()
    return User(*row) if row else None

@app.route('/', methods=['GET', 'POST'])
def login():
    # ... igual ao anterior ...
    pass

@app.route('/register', methods=['GET', 'POST'])
def register():
    # ... igual ao anterior ...
    pass

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
    # Aqui pode buscar o QR code do Node.js se desejar mostrar durante login
    pass

# --- NOVO: Recebe mensagem do Node.js e responde usando IA ---
@app.route("/whatsapp-message", methods=["POST"])
def whatsapp_message():
    data = request.json
    user = data.get("user")
    texto = data.get("texto")
    from_jid = data.get("from")

    if not user or not texto:
        return jsonify({"erro": "Dados ausentes"}), 400

    # --- Chamar IA (exemplo com OpenAI) ---
    resposta = gerar_resposta_ia(texto, user)

    return jsonify({"resposta": resposta})

def gerar_resposta_ia(texto, user):
    # Lógica customizada: pode usar usuário para personalizar
    # Exemplo com OpenAI (substitua por seu modelo real):
    openai.api_key = "SUA_OPENAI_API_KEY"
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Você é um assistente para um síndico."},
                {"role": "user", "content": texto}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return "Desculpe, ocorreu um erro ao gerar a resposta."

# Outras rotas, cadastro, etc.

if __name__ == "__main__":
    app.run(debug=True)

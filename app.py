from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from datetime import datetime
import requests
import openai  # ou sua biblioteca IA favorita

app = Flask(__name__)
app.secret_key = 'ALkcjYhUd876887FHnnfhfhYTd77f677f_f746HJcufiks8Mjs'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

# Configuração do Postgres
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
    cur.execute("SELECT id, username, password FROM usuarios WHERE id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()
    return User(*row) if row else None

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password FROM usuarios WHERE username = %s", (username,))
        row = cur.fetchone()
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
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO usuarios (username, password, plano, whatsapp_pai, telefones_monitorados) VALUES (%s, %s, %s, %s, %s)",
                        (username, password, 'Gratuito', 'whatsapp_pai', ['whatsapp_filho1', 'whatsapp_filho2']))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
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
    conn = get_db()
    cur = conn.cursor()

    # Recupera dados do usuário logado
    cur.execute("SELECT plano, telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    row = cur.fetchone()
    conn.close()

    plano = row[0]
    filhos_raw = row[1] or []
    filhos = [{"id": idx + 1, "numero_whatsapp": numero} for idx, numero in enumerate(filhos_raw)]

    # Define o limite de filhos com base no plano
    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    # Recupera o QR Code (se tiver)
    qr_code_url = f"http://147.93.4.219:3000/qrcode/{current_user.username}"
    qr_code = None
    try:
        r = requests.get(qr_code_url, timeout=10)
        qr_code = r.json().get("qrcode")
    except Exception:
        pass

    return render_template(
        "painel.html",
        session_id=current_user.username,
        plano=plano,
        filhos=filhos,
        max_filhos=max_filhos,
        qr_code=qr_code
    )

@app.route("/excluir-filho/<int:filho_id>", methods=["POST"])
@login_required
def excluir_filho(filho_id):
    conn = get_db()
    cur = conn.cursor()

    # Busca os números monitorados atuais
    cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    if not resultado:
        conn.close()
        return redirect(url_for("painel"))

    filhos = resultado[0] or []
    if filho_id <= len(filhos):
        del filhos[filho_id - 1]  # Remove o filho pelo índice
        cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))
        conn.commit()

    conn.close()
    return redirect(url_for("painel"))

@app.route("/adicionar-filho", methods=["POST"])
@login_required
def adicionar_filho():
    nome = request.form["nome"]
    numero = request.form["numero"]

    conn = get_db()
    cur = conn.cursor()

    # Busca os filhos atuais
    cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    filhos = resultado[0] if resultado and resultado[0] else []

    # Limite de filhos por plano
    cur.execute("SELECT plano FROM usuarios WHERE id = %s", (current_user.id,))
    plano = cur.fetchone()[0]
    max_filhos = 1 if plano == "Gratuito" else 10  # ajuste os limites conforme seu sistema

    if len(filhos) >= max_filhos:
        conn.close()
        return render_template("painel.html", erro="Limite de filhos atingido.", session_id=current_user.username, plano=plano, filhos=filhos, max_filhos=max_filhos)

    # Adiciona novo filho
    novo_filho = {"nome": nome, "numero_whatsapp": numero}
    filhos.append(novo_filho)
    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))
    conn.commit()
    conn.close()

    # Requisição do QR Code
    try:
        qr_response = requests.get(f"http://147.93.4.219:3000/qrcode/{numero}", timeout=10)
        qr_code = qr_response.json().get("qrcode")
    except Exception:
        qr_code = None

    return render_template("painel.html", session_id=current_user.username, plano=plano, filhos=filhos, max_filhos=max_filhos, qr_code=qr_code)

@app.route("/mensagem-recebida", methods=["POST"])
def mensagem_recebida():
    data = request.get_json()
    numero_filho = data.get("de")
    numero_contato = data.get("para")
    conteudo = data.get("texto")
    horario_str = data.get("horario")

    if not numero_filho or not numero_contato or not conteudo or not horario_str:
        return jsonify({"erro": "dados incompletos"}), 400

    try:
        # Converte string ISO para objeto datetime (substitui o 'Z' por fuso horário UTC)
        horario = datetime.fromisoformat(horario_str.replace("Z", "+00:00"))
    except Exception as e:
        return jsonify({"erro": f"formato de horário inválido: {str(e)}"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO mensagens_monitoradas (
            numero_filho, tipo, numero_contato, conteudo, horario
        ) VALUES (%s, %s, %s, %s, %s)
    """, (numero_filho, 'recebida', numero_contato, conteudo, horario))
    conn.commit()
    conn.close()

    return jsonify({"status": "mensagem salva com sucesso"})

@app.route("/disparar-relatorios", methods=["GET"])
def disparar_relatorios():
    conn = get_db()
    cur = conn.cursor()

    # Seleciona todos os usuários com seus filhos e WhatsApp do pai
    cur.execute("SELECT whatsapp_pai, telefones_monitorados FROM usuarios WHERE whatsapp_pai IS NOT NULL")
    usuarios = cur.fetchall()

    for whatsapp_pai, filhos in usuarios:
        if not filhos or len(filhos) == 0:
            continue

        for filho in filhos:
            numero_filho = filho if isinstance(filho, str) else filho.get("numero_whatsapp", "")
            if not numero_filho:
                continue

            # Busca mensagens do filho
            cur.execute("""
                SELECT conteudo FROM mensagens_monitoradas
                WHERE numero_filho = %s AND tipo = 'recebida'
            """, (numero_filho,))
            mensagens = cur.fetchall()

            if not mensagens:
                continue

            conteudos = [m[0] for m in mensagens]
            corpo = f"Relatório de mensagens do número {numero_filho}:\n" + "\n".join(conteudos)

            try:
                requests.post("http://147.93.4.219:3000/enviar-relatorio", json={
                    "numero_destino": whatsapp_pai,
                    "mensagem": corpo
                })
            except Exception as e:
                print(f"Erro ao enviar relatório para {whatsapp_pai}: {e}")

    conn.close()
    return jsonify({"status": "relatórios enviados com sucesso"})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

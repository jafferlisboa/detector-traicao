from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
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
    # Exemplo de geração do QR Code e exibição na tela
    qr_code_url = f"http://147.93.4.219:3000/qrcode/{current_user.username}"

    qr_data_url = None
    try:
        r = requests.get(qr_code_url, timeout=10)
        qrcode_base64 = r.json().get("qrcode")
        qr_data_url = qrcode_base64
    except Exception:
        qr_data_url = None

    return render_template("painel.html", session_id=current_user.username, qr_code=qr_data_url)

@app.route("/whatsapp-message", methods=["POST"])
def whatsapp_message():
    data = request.json
    user = data.get("user")
    texto = data.get("texto")
    from_jid = data.get("from")

    # Aqui você pode implementar lógica de inteligência artificial, por exemplo, usando OpenAI
    resposta = gerar_resposta_ia(texto, user)

    # Salva a mensagem no banco de dados
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO mensagens_monitoradas (numero_filho, tipo, numero_contato, conteudo, whatsapp_oficial) VALUES (%s, %s, %s, %s, %s)",
                (from_jid, 'recebida', user, texto, '5567992342051'))  # O WhatsApp Oficial do sistema
    conn.commit()
    conn.close()

    return jsonify({"resposta": resposta})

def gerar_resposta_ia(texto, user):
    openai.api_key = "SUA_OPENAI_API_KEY"
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": f"Você é um assistente para o síndico {user}."},
                {"role": "user", "content": texto}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return "Desculpe, ocorreu um erro ao gerar a resposta."

# Rota para acessar as mensagens monitoradas a cada 5 minutos
@app.route("/relatorio-pai", methods=["GET"])
def relatorio_pai():
    # Recupera as mensagens dos filhos para enviar para o WhatsApp do pai
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mensagens_monitoradas WHERE whatsapp_oficial = %s", ('5567992342051',))  # WhatsApp Oficial
    mensagens = cur.fetchall()
    conn.close()

    # Organiza as mensagens (por exemplo, por filho)
    mensagens_por_filho = {}
    for msg in mensagens:
        nome_filho = msg[0]  # Número do filho (adapte conforme seu banco de dados)
        if nome_filho not in mensagens_por_filho:
            mensagens_por_filho[nome_filho] = []
        mensagens_por_filho[nome_filho].append(msg[3])  # Adiciona o conteúdo da mensagem

    # Aqui, você pode enviar o relatório via WhatsApp para o pai
    for nome_filho, msgs in mensagens_por_filho.items():
        mensagens_combinadas = "\n".join(msgs)
        enviar_para_pai('5567992342051', f"Relatório de mensagens do(a) {nome_filho}:\n{mensagens_combinadas}")

    return jsonify({"status": "relatório enviado com sucesso"})

def enviar_para_pai(whatsapp_pai, mensagem):
    # Função para enviar mensagem ao WhatsApp do pai (via WhatsApp oficial)
    # Aqui você pode integrar com seu sistema para enviar a mensagem
    print(f"Enviando mensagem para o WhatsApp {whatsapp_pai}: {mensagem}")
    # Adicione aqui a integração com a API do WhatsApp para envio

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from datetime import datetime, timedelta
import requests
from openai import OpenAI
import tempfile
import os
import re
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT")
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = '/'

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
                        (username, password, 'Gratuito', username, []))
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
    cur.execute("SELECT plano, telefones_monitorados, whatsapp_pai FROM usuarios WHERE id = %s", (current_user.id,))
    row = cur.fetchone()
    plano = row[0]
    telefones_monitorados = row[1] or []
    whatsapp_pai = row[2]
    conn.close()

    filhos = []
    nomes_filhos = {}
    for idx, entry in enumerate(telefones_monitorados):
        nome, numero = parse_filho_entry(entry)
        filhos.append({"id": idx + 1, "numero_whatsapp": numero, "nome": nome})
        if numero:
            nomes_filhos[numero] = nome
        else:
            nomes_filhos[nome] = nome

    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    return render_template(
        "painel.html",
        session_id=whatsapp_pai,
        plano=plano,
        filhos=filhos,
        max_filhos=max_filhos,
        nomes_filhos=nomes_filhos
    )

def parse_filho_entry(entry):
    """Parseia uma entrada no formato 'nome @% numero' ou 'nome @% '"""
    if not entry:
        return "", ""
    parts = entry.split(" @% ")
    nome = parts[0]
    numero = parts[1] if len(parts) > 1 else ""
    return nome, numero

@app.route("/excluir-filho/<int:filho_id>", methods=["POST"])
@login_required
def excluir_filho(filho_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    if not resultado:
        conn.close()
        return redirect(url_for("painel"))

    telefones_monitorados = resultado[0] or []
    if filho_id <= len(telefones_monitorados):
        numero_filho = parse_filho_entry(telefones_monitorados[filho_id - 1])[1]
        telefones_monitorados.pop(filho_id - 1)
        cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (telefones_monitorados, current_user.id))
        
        if numero_filho:
            try:
                response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": numero_filho}, timeout=10)
                response.raise_for_status()
            except Exception as e:
                cur.execute(
                    "INSERT INTO log_erros (usuario_id, erro, data) VALUES (%s, %s, %s)",
                    (current_user.id, f"Erro ao excluir sessão: {str(e)}", datetime.now())
                )
    
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

@app.route("/adicionar-filho", methods=["POST"])
@login_required
def adicionar_filho():
    nome = request.form.get("nome")
    if not nome:
        return "Nome do filho é obrigatório.", 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plano, telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    plano = resultado[0]
    telefones_monitorados = resultado[1] or []

    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    if len([entry for entry in telefones_monitorados if parse_filho_entry(entry)[1]]) >= max_filhos:
        conn.close()
        return render_template(
            "painel.html",
            erro="Limite de filhos atingido.",
            session_id=current_user.username,
            plano=plano,
            filhos=[],
            max_filhos=max_filhos,
            nomes_filhos={}
        )

    telefones_monitorados.append(f"{nome} @% ")
    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (telefones_monitorados, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

@app.route("/status-conexao", methods=["POST"])
@login_required
def status_conexao():
    try:
        numeros = request.json.get("numeros", [])
        status_resultados = {}
        for numero in numeros:
            try:
                resp = requests.get(f"http://147.93.4.219:3000/status-sessao/{numero}", timeout=5)
                dados = resp.json()
                status_resultados[numero] = dados.get("conectado", False)
            except:
                status_resultados[numero] = False
        return jsonify(status_resultados)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/desconectar/<numero>", methods=["POST"])
@login_required
def desconectar(numero):
    try:
        response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": numero}, timeout=10)
        response.raise_for_status()
        return jsonify({"status": "sessão desconectada com sucesso"})
    except Exception as e:
        return jsonify({"erro": f"erro ao desconectar sessão: {str(e)}"}), 500

@app.route("/confirmar-conexao", methods=["POST"])
@login_required
def confirmar_conexao():
    data = request.get_json()
    numero = data.get("numero")
    nome = data.get("nome")
    if not numero or not nome:
        return jsonify({"erro": "Dados incompletos"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    telefones_monitorados = resultado[0] if resultado else []

    for i, entry in enumerate(telefones_monitorados):
        entry_nome, entry_numero = parse_filho_entry(entry)
        if entry_nome == nome and not entry_numero:
            telefones_monitorados[i] = f"{nome} @% {numero}"
            break
    else:
        return jsonify({"erro": "Nome não encontrado ou já possui número associado"}), 400

    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (telefones_monitorados, current_user.id))
    conn.commit()
    conn.close()
    return jsonify({"status": "salvo"})

@app.route("/solicitar-qrcode/<nome>")
@login_required
def solicitar_qrcode(nome):
    try:
        resp = requests.get(f"http://147.93.4.219:3000/qrcode/{nome}?force=true", timeout=10)
        dados = resp.json()
        return jsonify({"qrcode": dados.get("qrcode")})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/mensagem-recebida", methods=["POST"])
def mensagem_recebida():
    if request.content_type.startswith("multipart/form-data"):
        numero_filho = '+' + request.form.get("para", "").strip('@s.whatsapp.net')
        numero_contato = '+' + request.form.get("de", "").strip('@s.whatsapp.net')
        horario_str = request.form.get("horario")
        tipo = request.form.get("tipo")
        nome_contato = request.form.get("nome_contato", "")

        if "audio" not in request.files:
            return jsonify({"erro": "arquivo de áudio não encontrado"}), 400

        audio_file = request.files["audio"]
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp:
                audio_path = temp.name
                audio_file.save(audio_path)
            with open(audio_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="text"
                )
            conteudo = transcription.strip()
        except Exception as e:
            return jsonify({"erro": f"falha ao transcrever áudio: {str(e)}"}), 500
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
    else:
        data = request.get_json()
        numero_filho = '+' + data.get("para").strip('@s.whatsapp.net')
        numero_contato = '+' + data.get("de").strip('@s.whatsapp.net')
        conteudo = data.get("texto")
        horario_str = data.get("horario")
        tipo = data.get("tipo")

    if not all([numero_filho, numero_contato, conteudo, horario_str]):
        return jsonify({"erro": "dados incompletos"}), 400

    try:
        horario = datetime.fromisoformat(horario_str.replace("Z", "+00:00"))
    except Exception as e:
        return jsonify({"erro": f"formato de horário inválido: {str(e)}"}), 400

    if tipo not in ["recebida", "enviada"]:
        return jsonify({"erro": "tipo inválido"}), 400

    if numero_contato == "+556792342051":
        return jsonify({"status": "mensagem ignorada (destinatário oficial)"})

    conn = get_db()
    cur = conn.cursor()
    if numero_filho != '+556792342051':
        cur.execute(
            "INSERT INTO mensagens_monitoradas (numero_filho, tipo, numero_contato, conteudo, horario) VALUES (%s, %s, %s, %s, %s)",
            (numero_filho, tipo, numero_contato, conteudo, horario)
        )
        conn.commit()
    conn.close()
    return jsonify({"status": "mensagem salva com sucesso"})

@app.route("/disparar-relatorios", methods=["GET"])
def disparar_relatorios():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, whatsapp_pai, telefones_monitorados FROM usuarios WHERE whatsapp_pai IS NOT NULL")
        usuarios = cur.fetchall()
        for user_id, whatsapp_pai, telefones_monitorados in usuarios:
            if not telefones_monitorados:
                continue
            for entry in telefones_monitorados:
                _, numero_filho = parse_filho_entry(entry)
                if not numero_filho:
                    continue
                cur.execute(
                    "SELECT conteudo, horario FROM mensagens_monitoradas WHERE numero_filho = %s ORDER BY horario DESC",
                    (numero_filho,)
                )
                mensagens = cur.fetchall()
                if not mensagens:
                    continue
                corpo = f"Relatório de mensagens do número {numero_filho}:\n"
                for conteudo, horario in mensagens:
                    corpo += f"[{horario.strftime('%d/%m/%Y %H:%M')}] {conteudo}\n"
                try:
                    response = requests.post(
                        "http://147.93.4.219:3000/enviar-relatorio",
                        json={"numero_destino": whatsapp_pai, "mensagem": corpo},
                        timeout=10
                    )
                    response.raise_for_status()
                    cur.execute(
                        "DELETE FROM mensagens_monitoradas WHERE numero_filho = %s",
                        (numero_filho,)
                    )
                    conn.commit()
                except Exception as e:
                    cur.execute(
                        "INSERT INTO log_erros (usuario_id, erro, data) VALUES (%s, %s, %s)",
                        (user_id, str(e), datetime.now())
                    )
                    conn.commit()
    except Exception as e:
        return jsonify({"erro": "Erro interno no servidor", "detalhes": str(e)}), 500
    finally:
        conn.close()
    return jsonify({"status": "relatórios processados"})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

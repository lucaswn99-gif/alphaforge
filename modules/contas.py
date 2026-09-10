"""Contas, sessões e medição de uso.

Três decisões que valem explicação:

  - **Sem dependência nova.** O hash de senha usa `hashlib.scrypt`, da
    biblioteca padrão. Trazer passlib/bcrypt para o droplet significaria mais
    uma roda para travar versão e mais uma coisa para quebrar no deploy —
    scrypt resolve com a mesma força e zero instalação.

  - **Sessão opaca, não JWT.** O token é um número aleatório guardado como
    hash; sair de verdade é apagar a linha. Com JWT, "sair" é uma ficção do
    lado do cliente até o token expirar, e revogar exige a mesma tabela que o
    JWT prometia evitar.

  - **Anônimo continua funcionando.** A ficha do app na Play Store declara que
    nenhuma parte exige login. Quem não tem conta usa o plano gratuito com a
    cota contada por IP; a conta serve para levar a cota entre aparelhos e para
    assinar. Um muro de login aqui contradiria a declaração e reprovaria o app
    na revisão.
"""

import hashlib
import os
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:  # noqa: BLE001 — Windows sem tzdata
    FUSO = timezone(timedelta(hours=-3))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_BANCO = os.path.join(BASE_DIR, "contas.db")

DIAS_SESSAO = 60
NOME_COOKIE = "af_sessao"

# Parâmetros do scrypt. n=2^14 com r=8 pede 16 MB por verificação e leva
# ~100 ms: caro o bastante para inviabilizar força bruta, barato o bastante
# para um login não travar o worker.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_lock_init = threading.Lock()
_iniciado = False


# --------------------------------------------------------------------------
# Banco
# --------------------------------------------------------------------------

def _conectar():
    conexao = sqlite3.connect(CAMINHO_BANCO, timeout=10)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA journal_mode=WAL")
    conexao.execute("PRAGMA busy_timeout=5000")
    return conexao


def iniciar():
    """Cria o esquema uma vez por processo. Idempotente."""
    global _iniciado
    if _iniciado:
        return
    with _lock_init:
        if _iniciado:
            return
        with _conectar() as cx:
            cx.executescript("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    email       TEXT    NOT NULL UNIQUE,
                    senha_hash  TEXT    NOT NULL,
                    salt        TEXT    NOT NULL,
                    plano       TEXT    NOT NULL DEFAULT 'free',
                    plano_ate   TEXT,
                    criado_em   TEXT    NOT NULL,
                    ativo       INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS sessoes (
                    token_hash  TEXT    PRIMARY KEY,
                    usuario_id  INTEGER NOT NULL,
                    criada_em   TEXT    NOT NULL,
                    expira_em   TEXT    NOT NULL,
                    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessoes_usuario ON sessoes(usuario_id);

                CREATE TABLE IF NOT EXISTS uso_diario (
                    identidade  TEXT    NOT NULL,
                    recurso     TEXT    NOT NULL,
                    dia         TEXT    NOT NULL,
                    contagem    INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (identidade, recurso, dia)
                );
                CREATE INDEX IF NOT EXISTS idx_uso_dia ON uso_diario(dia);

                -- Uma linha por compra do Google Play. O token é a chave
                -- porque é com ele que a notificação do Play chega: sem este
                -- mapa, um aviso de cancelamento não teria como achar a conta.
                CREATE TABLE IF NOT EXISTS assinaturas (
                    token        TEXT    PRIMARY KEY,
                    usuario_id   INTEGER,
                    produto      TEXT,
                    estado       TEXT,
                    expira_em    TEXT,
                    order_id     TEXT,
                    reconhecida  INTEGER NOT NULL DEFAULT 0,
                    ativa        INTEGER NOT NULL DEFAULT 1,
                    criada_em    TEXT    NOT NULL,
                    atualizada_em TEXT   NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assin_usuario ON assinaturas(usuario_id);

                -- Pub/Sub entrega pelo menos uma vez: a mesma notificação pode
                -- chegar duas vezes. Guardar o id evita reprocessar.
                CREATE TABLE IF NOT EXISTS avisos_play (
                    id_mensagem  TEXT PRIMARY KEY,
                    visto_em     TEXT NOT NULL
                );
            """)
        _iniciado = True


def hoje():
    """Data corrente no fuso de Brasília — a cota vira à meia-noite daqui, não
    à meia-noite de UTC, que cairia às 21h do dia anterior."""
    return datetime.now(FUSO).date().isoformat()


def proxima_virada():
    """Instante em que a cota diária zera, em ISO com fuso."""
    agora = datetime.now(FUSO)
    amanha = agora.date() + timedelta(days=1)
    return datetime.combine(amanha, datetime.min.time(), tzinfo=FUSO).isoformat()


# --------------------------------------------------------------------------
# Senha
# --------------------------------------------------------------------------

def _derivar(senha, salt_bytes):
    return hashlib.scrypt(
        senha.encode("utf-8"), salt=salt_bytes,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    ).hex()


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalizar_email(email):
    return (email or "").strip().lower()


def senha_fraca(senha):
    """Devolve o motivo de recusa, ou None se a senha serve.

    Só comprimento. Regra de maiúscula-número-símbolo produz `Senha1!` e uma
    anotação no monitor; comprimento é o único critério que realmente move a
    dificuldade de quebrar o hash.
    """
    if not senha or len(senha) < 8:
        return "A senha precisa de pelo menos 8 caracteres."
    if len(senha) > 200:
        return "A senha passou de 200 caracteres."
    return None


# --------------------------------------------------------------------------
# Usuários
# --------------------------------------------------------------------------

def criar_usuario(email, senha):
    """Cria a conta. Devolve (usuario, None) ou (None, motivo)."""
    iniciar()
    email = normalizar_email(email)
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return None, "E-mail inválido."
    motivo = senha_fraca(senha)
    if motivo:
        return None, motivo

    salt = secrets.token_bytes(16)
    registro = {
        "email": email,
        "senha_hash": _derivar(senha, salt),
        "salt": salt.hex(),
        "criado_em": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _conectar() as cx:
            cursor = cx.execute(
                "INSERT INTO usuarios (email, senha_hash, salt, plano, criado_em)"
                " VALUES (:email, :senha_hash, :salt, 'free', :criado_em)", registro)
            usuario_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return None, "Já existe uma conta com esse e-mail."

    return buscar_usuario(usuario_id), None


def buscar_usuario(usuario_id):
    iniciar()
    with _conectar() as cx:
        linha = cx.execute(
            "SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (usuario_id,)).fetchone()
    return dict(linha) if linha else None


def autenticar(email, senha):
    """Confere e-mail e senha. Devolve o usuário ou None.

    A senha é verificada mesmo quando o e-mail não existe, contra um salt
    descartável: sem isso, a resposta volta na hora para e-mail inexistente e
    demora 100 ms para e-mail real — e o tempo de resposta vira um oráculo de
    quem tem conta aqui.
    """
    iniciar()
    email = normalizar_email(email)
    with _conectar() as cx:
        linha = cx.execute(
            "SELECT * FROM usuarios WHERE email = ? AND ativo = 1", (email,)).fetchone()

    if linha is None:
        _derivar(senha or "", secrets.token_bytes(16))
        return None

    esperado = linha["senha_hash"]
    obtido = _derivar(senha or "", bytes.fromhex(linha["salt"]))
    if not secrets.compare_digest(esperado, obtido):
        return None
    return dict(linha)


def plano_do_usuario(usuario):
    """Plano efetivo: 'premium' vencido volta a valer como 'free'."""
    if not usuario:
        return "free"
    if usuario.get("plano") != "premium":
        return "free"
    ate = usuario.get("plano_ate")
    if not ate:
        return "premium"
    try:
        if datetime.fromisoformat(ate) < datetime.now(timezone.utc):
            return "free"
    except (TypeError, ValueError):
        return "free"
    return "premium"


def definir_plano(usuario_id, plano, ate=None):
    """Muda o plano. Chamado pelo webhook de assinatura, nunca pela interface."""
    iniciar()
    with _conectar() as cx:
        cx.execute("UPDATE usuarios SET plano = ?, plano_ate = ? WHERE id = ?",
                   (plano, ate, usuario_id))
    return buscar_usuario(usuario_id)


# --------------------------------------------------------------------------
# Sessões
# --------------------------------------------------------------------------

def abrir_sessao(usuario_id):
    """Cria a sessão e devolve o token em claro — a única vez que ele existe
    fora do cookie. O banco guarda só o hash."""
    iniciar()
    token = secrets.token_urlsafe(32)
    agora = datetime.now(timezone.utc)
    with _conectar() as cx:
        cx.execute(
            "INSERT INTO sessoes (token_hash, usuario_id, criada_em, expira_em)"
            " VALUES (?, ?, ?, ?)",
            (_hash_token(token), usuario_id, agora.isoformat(),
             (agora + timedelta(days=DIAS_SESSAO)).isoformat()))
    return token


def usuario_da_sessao(token):
    if not token:
        return None
    iniciar()
    with _conectar() as cx:
        linha = cx.execute(
            "SELECT u.* FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id"
            " WHERE s.token_hash = ? AND s.expira_em > ? AND u.ativo = 1",
            (_hash_token(token), datetime.now(timezone.utc).isoformat())).fetchone()
    return dict(linha) if linha else None


def fechar_sessao(token):
    if not token:
        return
    iniciar()
    with _conectar() as cx:
        cx.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash_token(token),))


def limpar_expirados():
    """Varre sessões vencidas e uso de dias anteriores. Chamado na subida."""
    iniciar()
    corte = (date.today() - timedelta(days=7)).isoformat()
    with _conectar() as cx:
        cx.execute("DELETE FROM sessoes WHERE expira_em < ?",
                   (datetime.now(timezone.utc).isoformat(),))
        cx.execute("DELETE FROM uso_diario WHERE dia < ?", (corte,))
        # Ids de notificação só servem para barrar reenvio, que acontece em
        # minutos. Guardar 30 dias é folga de sobra.
        cx.execute("DELETE FROM avisos_play WHERE visto_em < ?",
                   ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))


# --------------------------------------------------------------------------
# Medição de uso
# --------------------------------------------------------------------------

def consultar_uso(identidade, recurso):
    iniciar()
    with _conectar() as cx:
        linha = cx.execute(
            "SELECT contagem FROM uso_diario WHERE identidade = ? AND recurso = ? AND dia = ?",
            (identidade, recurso, hoje())).fetchone()
    return int(linha["contagem"]) if linha else 0


def salvar_assinatura(token, usuario_id, leitura):
    """Grava (ou atualiza) o que o Google respondeu sobre um token.

    `usuario_id` pode vir None: é o caso da notificação que chega antes de a
    tela ter conseguido confirmar a compra. A linha nasce órfã e ganha dono
    quando o aplicativo restaurar a compra.
    """
    iniciar()
    agora = datetime.now(timezone.utc).isoformat()
    with _conectar() as cx:
        existente = cx.execute(
            "SELECT usuario_id FROM assinaturas WHERE token = ?", (token,)).fetchone()
        dono = usuario_id or (existente["usuario_id"] if existente else None)
        if existente:
            cx.execute(
                "UPDATE assinaturas SET usuario_id = ?, produto = ?, estado = ?,"
                " expira_em = ?, order_id = ?, reconhecida = ?, ativa = ?,"
                " atualizada_em = ? WHERE token = ?",
                (dono, leitura["produto"], leitura["estado"], leitura["expira_em"],
                 leitura["order_id"], int(leitura["reconhecida"]),
                 int(leitura["ativo"]), agora, token))
        else:
            cx.execute(
                "INSERT INTO assinaturas (token, usuario_id, produto, estado,"
                " expira_em, order_id, reconhecida, ativa, criada_em, atualizada_em)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (token, dono, leitura["produto"], leitura["estado"],
                 leitura["expira_em"], leitura["order_id"],
                 int(leitura["reconhecida"]), int(leitura["ativo"]), agora, agora))
    return dono


def assinatura(token):
    iniciar()
    with _conectar() as cx:
        linha = cx.execute("SELECT * FROM assinaturas WHERE token = ?", (token,)).fetchone()
    return dict(linha) if linha else None


def encerrar_assinatura(token):
    """Marca um token como não valendo mais. Usado quando o usuário troca de
    plano: o token novo aponta para o antigo, que precisa parar de contar."""
    iniciar()
    with _conectar() as cx:
        cx.execute("UPDATE assinaturas SET ativa = 0, atualizada_em = ? WHERE token = ?",
                   (datetime.now(timezone.utc).isoformat(), token))


def recalcular_plano(usuario_id):
    """Define o plano do usuário a partir das assinaturas que ele tem.

    A verdade é a soma das assinaturas, não o último evento recebido: quem tem
    uma cancelada válida até o mês que vem e outra recém-comprada continua
    Premium pela que valer mais longe.
    """
    if not usuario_id:
        return None
    iniciar()
    with _conectar() as cx:
        linha = cx.execute(
            "SELECT MAX(expira_em) AS ate FROM assinaturas"
            " WHERE usuario_id = ? AND ativa = 1", (usuario_id,)).fetchone()
    ate = linha["ate"] if linha else None
    if not ate:
        return definir_plano(usuario_id, "free", None)
    try:
        vencida = datetime.fromisoformat(ate.replace("Z", "+00:00")) < datetime.now(timezone.utc)
    except (TypeError, ValueError):
        vencida = False
    return definir_plano(usuario_id, "free" if vencida else "premium",
                         None if vencida else ate)


def aviso_ja_visto(id_mensagem):
    """True se esta notificação do Play já foi processada. O Pub/Sub entrega
    pelo menos uma vez — sem isto, um reenvio repetiria o trabalho."""
    if not id_mensagem:
        return False
    iniciar()
    try:
        with _conectar() as cx:
            cx.execute("INSERT INTO avisos_play (id_mensagem, visto_em) VALUES (?, ?)",
                       (id_mensagem, datetime.now(timezone.utc).isoformat()))
        return False
    except sqlite3.IntegrityError:
        return True


def registrar_uso(identidade, recurso):
    """Soma um ao contador do dia e devolve o total já com este uso incluído.

    UPSERT atômico: duas requisições simultâneas do mesmo usuário não podem
    ler 2, somar 1 cada e gravar 3 — o incremento acontece dentro do banco.
    """
    iniciar()
    with _conectar() as cx:
        cx.execute(
            "INSERT INTO uso_diario (identidade, recurso, dia, contagem)"
            " VALUES (?, ?, ?, 1)"
            " ON CONFLICT(identidade, recurso, dia)"
            " DO UPDATE SET contagem = contagem + 1",
            (identidade, recurso, hoje()))
        linha = cx.execute(
            "SELECT contagem FROM uso_diario WHERE identidade = ? AND recurso = ? AND dia = ?",
            (identidade, recurso, hoje())).fetchone()
    return int(linha["contagem"]) if linha else 1

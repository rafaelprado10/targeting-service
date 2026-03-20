import os
import sys
import requests
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.types.json import Json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from functools import wraps
import logging

# Configura o logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Carrega .env para desenvolvimento local
load_dotenv()

app = Flask(__name__)

# --- Configuração ---
DATABASE_URL = os.getenv("DATABASE_URL")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL")

if not DATABASE_URL or not AUTH_SERVICE_URL:
    log.critical("Erro: DATABASE_URL e AUTH_SERVICE_URL devem ser definidos.")
    sys.exit(1)

# --- Pool de Conexão com o Banco (psycopg3) ---
try:
    pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
    )
    log.info("Pool de conexões com o PostgreSQL",
             "(targeting) inicializado (psycopg3).")
except psycopg.OperationalError as e:
    log.critical(f"Erro fatal ao conectar ao PostgreSQL: {e}")
    sys.exit(1)


# --- Middleware de Autenticação (Idêntico ao flag-service) ---
def require_auth(f):
    """Middleware para validar a chave de API contra o auth-service"""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Authorization header obrigatório"}), 401

        try:
            validate_url = f"{AUTH_SERVICE_URL}/validate"
            response = requests.get(
                validate_url, headers={"Authorization": auth_header}, timeout=3
            )

            if response.status_code != 200:
                log.warning(
                    "Falha na validação da chave (status: ",
                    response.status_code, ")"
                )
                return jsonify({"error": "Chave de API inválida"}), 401

        except requests.exceptions.Timeout:
            log.error("Timeout ao conectar com o auth-service")
            return (
                jsonify({
                    "error": "Serviço de autenticação indisponível (timeout)"
                }),
                504,
            )
        except requests.exceptions.RequestException as e:
            log.error(f"Erro ao conectar com o auth-service: {e}")
            return jsonify({
                "error": "Serviço de autenticação indisponível"
            }), 503

        return f(*args, **kwargs)

    return decorated


# --- Endpoints da API ---


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/rules", methods=["POST"])
@require_auth
def create_rule():
    """Cria uma nova regra de segmentação para uma flag"""
    data = request.get_json()
    if not data or "flag_name" not in data or "rules" not in data:
        return jsonify({
            "error": "'flag_name' e 'rules' (JSON) são obrigatórios"
        }), 400

    flag_name = data["flag_name"]
    rules_obj = data["rules"]
    is_enabled = data.get("is_enabled", True)

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO targeting_rules (
                        flag_name, is_enabled, rules, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, NOW(), NOW())
                    RETURNING *
                    """,
                    (flag_name, is_enabled, Json(rules_obj)),
                )
                new_rule = cur.fetchone()

        log.info(f"Regra para '{flag_name}' criada com sucesso.")
        return jsonify(new_rule), 201

    except psycopg.errors.UniqueViolation:
        log.warning(f"Tentativa de criar regra duplicada: '{flag_name}'")
        return jsonify({
            "error": f"Regra para a flag '{flag_name}' já existe"
        }), 409

    except Exception as e:
        log.error(f"Erro ao criar regra: {e}")
        return jsonify({
            "error": "Erro interno do servidor", "details": str(e)
        }), 500


@app.route("/rules/<string:flag_name>", methods=["GET"])
@require_auth
def get_rule(flag_name):
    """Busca uma regra de segmentação pelo nome da flag"""
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM targeting_rules WHERE flag_name = %s",
                    (flag_name,)
                )
                rule = cur.fetchone()

        if not rule:
            return jsonify({"error": "Regra não encontrada"}), 404
        return jsonify(rule)

    except Exception as e:
        log.error(f"Erro ao buscar regra '{flag_name}': {e}")
        return jsonify({
            "error": "Erro interno do servidor", "details": str(e)
        }), 500


@app.route("/rules/<string:flag_name>", methods=["PUT"])
@require_auth
def update_rule(flag_name):
    """Atualiza a regra de segmentação de uma flag"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo da requisição obrigatório"}), 400

    fields = []
    values = []

    if "rules" in data:
        fields.append("rules = %s")
        values.append(Json(data["rules"]))
    if "is_enabled" in data:
        fields.append("is_enabled = %s")
        values.append(data["is_enabled"])

    if not fields:
        return (
            jsonify({
                "error": """Pelo menos um campo (
                    'rules', 'is_enabled'
                ) é obrigatório"""
            }),
            400,
        )

    values.append(flag_name)
    query = f"""UPDATE targeting_rules SET
            {', '.join(fields)},
            updated_at = NOW()
            WHERE flag_name = %s RETURNING *"""

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
                updated_rule = cur.fetchone()

        if not updated_rule:
            return jsonify({"error": "Regra não encontrada"}), 404

        log.info(f"Regra para '{flag_name}' atualizada com sucesso.")
        return jsonify(updated_rule), 200

    except Exception as e:
        log.error(f"Erro ao atualizar regra '{flag_name}': {e}")
        return jsonify({
            "error": "Erro interno do servidor", "details": str(e)
        }), 500


@app.route("/rules/<string:flag_name>", methods=["DELETE"])
@require_auth
def delete_rule(flag_name):
    """Deleta a regra de segmentação de uma flag"""
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM targeting_rules WHERE flag_name = %s",
                    (flag_name,)
                )
                deleted = cur.rowcount

        if deleted == 0:
            return jsonify({"error": "Regra não encontrada"}), 404

        log.info(f"Regra para '{flag_name}' deletada com sucesso.")
        return "", 204  # 204 No Content

    except Exception as e:
        log.error(f"Erro ao deletar regra '{flag_name}': {e}")
        return jsonify({
            "error": "Erro interno do servidor", "details": str(e)
        }), 500

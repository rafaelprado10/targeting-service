# syntax=docker/dockerfile:1.6

############################
# 1) Builder: cria venv e instala dependências
############################
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Se algum pacote precisar compilar (fallback), isso cobre
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


############################
# 2) Runtime: imagem final enxuta
############################
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Certificados para HTTPS (requests) + runtime mínimo
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copia o venv pronto
COPY --from=builder /opt/venv /opt/venv

# Copia a aplicação
COPY . .

# Usuário não-root
RUN addgroup --system app && adduser --system --ingroup app app && \
    chown -R app:app /app

USER app

EXPOSE 8003

# Ajuste workers conforme CPU/memória (exemplo: 2 workers)
CMD ["gunicorn", "--bind", "0.0.0.0:8003", "app:app"]
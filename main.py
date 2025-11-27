import os
from fastapi import FastAPI, Header, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Optional

DB_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("API_KEY", "minha-chave-teste")

if not DB_URL:
    raise RuntimeError("DATABASE_URL não configurada (env var)")

app = FastAPI(title="Monitor Collector API")

class Payload(BaseModel):
    client_id: str
    timestamp: str
    cpu: float
    ram: float

def get_conn():
    return psycopg2.connect(DB_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS metricas (
        id SERIAL PRIMARY KEY,
        client_id TEXT NOT NULL,
        ts TIMESTAMP NOT NULL,
        cpu REAL NOT NULL,
        ram REAL NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS comandos (
        client_id TEXT PRIMARY KEY,
        command TEXT,
        updated_at TIMESTAMP
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

# Agent -> envia medições
@app.post("/api/collect")
def collect(payload: Payload, authorization: Optional[str] = Header(None)):
    # opcional: validar authorization se quiser
    try:
        ts = datetime.fromisoformat(payload.timestamp)
    except Exception:
        ts = datetime.utcnow()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO metricas (client_id, ts, cpu, ram) VALUES (%s, %s, %s, %s)",
        (payload.client_id, ts, payload.cpu, payload.ram)
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status":"ok"}

# Agent -> consulta comando (polling)
@app.get("/api/command/{client_id}")
def get_command(client_id: str):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT command, updated_at FROM comandos WHERE client_id = %s", (client_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {"command": "stop"}
    return row

# Admin web -> setar comando para um client (start/stop)
@app.post("/api/command/{client_id}")
def set_command(client_id: str, command: str = Form(...), authorization: Optional[str] = Header(None)):
    if not authorization or API_KEY not in authorization:
        raise HTTPException(401, "Unauthorized")
    if command not in ("start", "stop"):
        raise HTTPException(400, "Invalid command")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO comandos (client_id, command, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (client_id) DO UPDATE SET command = EXCLUDED.command, updated_at = EXCLUDED.updated_at;
    """, (client_id, command))
    conn.commit()
    cur.close()
    conn.close()
    return {"status":"ok", "client_id": client_id, "command": command}

# Endpoint para retornar métricas (JSON) usadas pelo dashboard
@app.get("/api/metrics/{client_id}")
def metrics(client_id: str, limit: int = 500):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, client_id, ts, cpu, ram
        FROM metricas
        WHERE client_id = %s
        ORDER BY ts DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    rows.reverse()
    return JSONResponse(content=rows)

# Simple control page (browser) to send start/stop (requests will ask API_KEY)
@app.get("/", response_class=HTMLResponse)
def control_page():
    return HTMLResponse("""
    <html>
      <head><meta charset='utf-8'><title>Controle Monitor</title></head>
      <body style="font-family:Arial;max-width:720px;margin:20px;">
        <h2>Controle do Monitor</h2>
        <label>Client ID: <input id="client" value="pc-001"></label><br/><br/>
        <label>API Key: <input id="apikey" value=""></label><br/><br/>
        <button onclick="send('start')">Iniciar</button>
        <button onclick="send('stop')">Parar</button>
        <script>
          async function send(cmd){
            const client = document.getElementById('client').value;
            const key = document.getElementById('apikey').value;
            const form = new FormData();
            form.append('command', cmd);
            const res = await fetch('/api/command/' + client, {
              method: 'POST',
              headers: { 'Authorization': 'Bearer ' + key },
              body: form
            });
            const j = await res.json();
            alert('Resposta: ' + JSON.stringify(j));
          }
        </script>
      </body>
    </html>
    """)


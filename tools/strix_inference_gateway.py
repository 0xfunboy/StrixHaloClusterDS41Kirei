#!/usr/bin/env python3
"""
Strix Inference Gateway - Multi-User Arbitrated Edition
Unified OpenAI-compatible API Gateway for Dual AMD Strix Halo Cluster.
Supports VS Code GitHub Copilot Chat and external access via inference.yed1z.ai.

Key Features:
1. Automatic Model Selection: Scegliendo il modello da Copilot (ds41-abliterated-q2-imatrix o qwen38-uncensored-gufo),
   il gateway rileva automaticamente il target e lo carica sul cluster se necessario.
2. Concurrency & Arbitration (Anti-Collisione Roma/Livorno):
   Se l'Utente A sta generando attivamente su DeepSeek e l'Utente B seleziona Qwen:
   - La generazione di A NON viene interrotta o corrotta.
   - La richiesta di B attende il completamento (drain) di A.
   - Non appena A termina, il gateway effettua lo switch sicuro della memoria GPU e serve B.
   - Se A ha un job lunghissimo che supera il timeout di attesa (45s), B riceve HTTP 429 con messaggio chiaro.
3. Stream Multiplexing & In-Flight Tracking:
   Richieste concorrenti per lo STESSO modello attivo sono processate simultaneamente in continuous batching.
4. Token Authentication:
   Le richieste esterne su inference.yed1z.ai richiedono token Bearer valido (reiezione 401 per chiamate non autorizzate).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

KEYS_FILE = Path("/home/funboy/.config/strix-inference/api_keys.json")
DEEPSEEK_PORT = 18231
QWEN_PORT = 18562
GATEWAY_PRIMARY_PORT = 8081
GATEWAY_SECONDARY_PORT = 18561

MODEL_DEEPSEEK_ID = "ds41-abliterated-q2-imatrix"
MODEL_QWEN_ID = "qwen38-uncensored-gufo"

# State & Arbitration locks
STATE_LOCK = threading.RLock()
SWITCH_LOCK = threading.Lock()

IN_FLIGHT_COUNT = 0
IN_FLIGHT_MODEL = None
LAST_ACTIVITY_TIME = 0.0


def load_authorized_keys() -> set[str]:
    keys = {"-761c2212", "7fa3e51b", "sk-yed1z-cluster-halo-7f8a9b", "sk-yed1z-master-2026"}
    if KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text())
            for k in data.get("keys", []):
                if isinstance(k, str) and k.strip():
                    keys.add(k.strip())
        except Exception as e:
            print(f"[GATEWAY] Warning reading keys file: {e}", file=sys.stderr)
    return keys


def is_port_ready(port: int, model_sub: str) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/v1/models")
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", errors="ignore")
        conn.close()
        return resp.status == 200 and model_sub.lower() in data.lower()
    except Exception:
        return False


def get_currently_active_model() -> str | None:
    if is_port_ready(DEEPSEEK_PORT, "deepseek"):
        return "deepseek"
    if is_port_ready(QWEN_PORT, "qwen"):
        return "qwen"
    return None


@contextlib.contextmanager
def request_in_flight_scope(model_name: str):
    global IN_FLIGHT_COUNT, IN_FLIGHT_MODEL, LAST_ACTIVITY_TIME
    with STATE_LOCK:
        IN_FLIGHT_COUNT += 1
        IN_FLIGHT_MODEL = model_name
        LAST_ACTIVITY_TIME = time.time()
    try:
        yield
    finally:
        with STATE_LOCK:
            IN_FLIGHT_COUNT = max(0, IN_FLIGHT_COUNT - 1)
            if IN_FLIGHT_COUNT == 0:
                IN_FLIGHT_MODEL = None
            LAST_ACTIVITY_TIME = time.time()


def acquire_model(target: str, max_wait_sec: float = 45.0) -> tuple[bool, str, int]:
    """
    Ensure target model is loaded and ready.
    If another model is actively generating, waits for in-flight requests to drain.
    Returns (success, message, http_status_code).
    """
    # Quick check without switch lock
    with STATE_LOCK:
        active = get_currently_active_model()
        if active == target:
            return True, f"Modello {target} già attivo", 200

    # Acquire switch lock for arbitration
    if not SWITCH_LOCK.acquire(timeout=max_wait_sec):
        return False, "Operazione di switch temporaneamente bloccata da un'altra richiesta concorrente. Riprova tra pochi istanti.", 429

    try:
        # Re-check active model
        active = get_currently_active_model()
        if active == target:
            return True, f"Modello {target} già attivo", 200

        # Another model is active - check if it is busy with active requests
        if active is not None:
            drain_start = time.time()
            while True:
                with STATE_LOCK:
                    busy = IN_FLIGHT_COUNT
                    busy_model = IN_FLIGHT_MODEL
                if busy == 0:
                    break
                elapsed = time.time() - drain_start
                if elapsed > max_wait_sec:
                    return False, (
                        f"Il cluster è attualmente occupato: {busy} generazione/i attiva/e su {busy_model} "
                        f"da parte di un altro utente. Attendi il completamento prima di cambiare modello."
                    ), 429
                time.sleep(0.3)

        # In-flight requests drained! Now perform safe model switch
        print(f"[GATEWAY] Esecuzione switch atomico sicuro: {active or 'nessuno'} -> {target}...", flush=True)
        res = subprocess.run(["/home/funboy/.local/bin/cluster-model", target], capture_output=True, text=True)
        
        # Verify readiness
        target_port = DEEPSEEK_PORT if target == "deepseek" else QWEN_PORT
        target_sub = "deepseek" if target == "deepseek" else "qwen"
        if res.returncode == 0 and is_port_ready(target_port, target_sub):
            print(f"[GATEWAY] Switch completato con successo: {target} è ora operativo.", flush=True)
            return True, f"Modello {target} attivato con successo", 200
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"[GATEWAY] Errore avvio modello {target}: {err}", flush=True)
            return False, f"Impossibile avviare il modello {target}: {err}", 503

    finally:
        SWITCH_LOCK.release()


class ArbitratedGatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def is_external_request(self) -> bool:
        if self.headers.get("cf-connecting-ip") or self.headers.get("cf-ray") or self.headers.get("x-forwarded-for"):
            return True
        host = self.headers.get("Host", "").lower()
        if "yed1z.ai" in host:
            return True
        return False

    def is_authorized(self) -> bool:
        if not self.is_external_request():
            return True

        auth = self.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        elif auth.startswith("bearer "):
            token = auth[7:].strip()
        elif self.headers.get("x-api-key"):
            token = self.headers.get("x-api-key", "").strip()

        authorized_keys = load_authorized_keys()
        return token in authorized_keys

    def send_json_error(self, code: int, message: str, err_type: str = "invalid_request_error"):
        payload = {
            "error": {
                "message": message,
                "type": err_type,
                "code": "unauthorized" if code == 401 else ("rate_limit_exceeded" if code == 429 else "server_error")
            }
        }
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path

        if path in ("/", "/health"):
            with STATE_LOCK:
                active = get_currently_active_model()
                busy = IN_FLIGHT_COUNT
                busy_model = IN_FLIGHT_MODEL
            res = {
                "status": "healthy" if active else "standby",
                "cluster": "Dual AMD Strix Halo (gfx1151 / 256GB Unified)",
                "active_model": active,
                "in_flight_requests": busy,
                "busy_model": busy_model,
                "models": [
                    {
                        "id": MODEL_DEEPSEEK_ID,
                        "status": "active" if active == "deepseek" else "standby",
                        "engine": "Kirei Custom HIP Engine with DSpark MXFP4",
                        "speed": "~50 tok/s decode"
                    },
                    {
                        "id": MODEL_QWEN_ID,
                        "status": "active" if active == "qwen" else "standby",
                        "engine": "Gufo v0.8.0 MTP Speculative",
                        "speed": "~42 tok/s decode"
                    }
                ]
            }
            body = json.dumps(res, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self.is_authorized():
            self.send_json_error(401, "Accesso negato: API key richiesta per inference.yed1z.ai. Fornisci 'Authorization: Bearer <secret-token>'", "authentication_error")
            return

        if path == "/v1/models":
            models_data = [
                {
                    "id": MODEL_DEEPSEEK_ID,
                    "object": "model",
                    "created": 1793220000,
                    "owned_by": "customendpoint",
                    "name": "DeepSeek V4.1 Flash Abliterated Q2 (Dual Strix 50 tok/s)",
                    "context_length": 32768,
                    "max_input_tokens": 24576,
                    "max_output_tokens": 6144
                },
                {
                    "id": MODEL_QWEN_ID,
                    "object": "model",
                    "created": 1793220000,
                    "owned_by": "customendpoint",
                    "name": "Qwen 3.8 Flash Next Uncensored Gufo",
                    "context_length": 187392,
                    "max_input_tokens": 187392,
                    "max_output_tokens": 8192
                },
                {
                    "id": "deepseek-v4.1-flash",
                    "object": "model",
                    "created": 1793220000,
                    "owned_by": "customendpoint",
                    "name": "DeepSeek V4.1 Flash",
                    "context_length": 32768
                }
            ]
            resp_body = json.dumps({"object": "list", "data": models_data}, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            return

        self.send_json_error(404, f"Endpoint {path} non trovato")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path

        if not self.is_authorized():
            self.send_json_error(401, "Accesso negato: API key richiesta per inference.yed1z.ai. Fornisci 'Authorization: Bearer <secret-token>'", "authentication_error")
            return

        if path != "/v1/chat/completions":
            self.send_json_error(404, f"Endpoint {path} non supportato")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)

        try:
            payload = json.loads(raw_body)
        except Exception:
            self.send_json_error(400, "Payload JSON non valido")
            return

        req_model = payload.get("model", "")
        server_port = self.server.server_port

        # Determine target model
        target = "deepseek"
        if req_model == MODEL_QWEN_ID or (server_port == GATEWAY_SECONDARY_PORT and req_model != MODEL_DEEPSEEK_ID):
            target = "qwen"

        # Concurrency & Arbitration: Safe acquisition without interrupting other users
        ok, msg, status_code = acquire_model(target, max_wait_sec=45.0)
        if not ok:
            self.send_json_error(status_code, msg, "cluster_arbitration_error")
            return

        # Prepare parameters for target engine
        if target == "deepseek":
            upstream_port = DEEPSEEK_PORT
            payload["context_tokens"] = 32768
            payload.setdefault("max_tokens", 6144)
            payload["reasoning_effort"] = "none"
            tpl = payload.get("chat_template_kwargs") or {}
            tpl["enable_thinking"] = False
            tpl["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = tpl
            payload["model"] = "deepseek-v4.1-flash"
        else:
            upstream_port = QWEN_PORT
            payload.setdefault("max_tokens", 8192)

        forward_body = json.dumps(payload).encode("utf-8")
        is_stream = bool(payload.get("stream", False))

        # Forward request inside request_in_flight_scope
        with request_in_flight_scope(target):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=600)
                headers = {
                    "Content-Type": "application/json",
                    "Accept": self.headers.get("Accept", "*/*"),
                    "Content-Length": str(len(forward_body))
                }
                conn.request("POST", "/v1/chat/completions", body=forward_body, headers=headers)
                resp = conn.getresponse()

                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in {"connection", "transfer-encoding", "content-length"}:
                        self.send_header(h, v)

                if is_stream:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()

                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        if req_model and req_model != "deepseek-v4.1-flash":
                            chunk = chunk.replace(b'"model":"deepseek-v4.1-flash"', f'"model":"{req_model}"'.encode("utf-8"))
                        chunk_header = f"{len(chunk):X}\r\n".encode("ascii")
                        self.wfile.write(chunk_header + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                else:
                    resp_data = resp.read()
                    if req_model and req_model != "deepseek-v4.1-flash":
                        resp_data = resp_data.replace(b'"model":"deepseek-v4.1-flash"', f'"model":"{req_model}"'.encode("utf-8"))
                    self.send_header("Content-Length", str(len(resp_data)))
                    self.end_headers()
                    self.wfile.write(resp_data)
                    self.wfile.flush()

                conn.close()
            except Exception as e:
                if not self.wfile.closed:
                    self.send_json_error(502, f"Errore motore cluster su porta {upstream_port}: {e}")

    def log_message(self, format: str, *args: object) -> None:
        remote = self.headers.get("cf-connecting-ip") or self.address_string()
        print(f"[GATEWAY:{self.server.server_port}] {remote} {format % args}", flush=True)


def run_server(port: int):
    server = ThreadingHTTPServer(("0.0.0.0", port), ArbitratedGatewayHandler)
    server.server_port = port
    print(f"[GATEWAY] In ascolto su 0.0.0.0:{port}...", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    print("[GATEWAY] Inizializzazione Strix Arbitrated Gateway...")
    print(f"[GATEWAY] DeepSeek upstream: 127.0.0.1:{DEEPSEEK_PORT}")
    print(f"[GATEWAY] Qwen upstream: 127.0.0.1:{QWEN_PORT}")

    t1 = threading.Thread(target=run_server, args=(GATEWAY_PRIMARY_PORT,), daemon=True)
    t2 = threading.Thread(target=run_server, args=(GATEWAY_SECONDARY_PORT,), daemon=True)

    t1.start()
    t2.start()

    print(f"[GATEWAY] Gateway online su porte {GATEWAY_PRIMARY_PORT} (Primaria/Cloudflare/DeepSeek) e {GATEWAY_SECONDARY_PORT} (Qwen).")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[GATEWAY] Spegnimento gateway.")

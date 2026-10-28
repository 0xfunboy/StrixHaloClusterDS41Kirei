#!/usr/bin/env python3
"""web-dashboard.py - Sleek web UI & chat playground for Dual Strix Halo DeepSeek V4.1 Flash."""

import http.client
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT = int(os.environ.get("DASHBOARD_PORT", "18232"))
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 18231

HTML_PAGE = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DeepSeek V4.1 Flash — Dual AMD Strix Halo Cluster</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090a0f;
      --card-bg: #12141c;
      --border: #232738;
      --accent: #3b82f6;
      --accent-glow: rgba(59, 130, 246, 0.25);
      --green: #10b981;
      --text: #f3f4f6;
      --muted: #9ca3af;
      --code-bg: #0d0e14;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 1rem 2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .logo {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, #3b82f6, #8b5cf6);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      color: white;
      font-size: 1rem;
    }
    .brand-title {
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }
    .badge-status {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      background: rgba(16, 185, 129, 0.1);
      color: var(--green);
      border: 1px solid rgba(16, 185, 129, 0.25);
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 8px var(--green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .container {
      max-width: 1300px;
      width: 100%;
      margin: 1.5rem auto;
      padding: 0 1.5rem;
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 1.5rem;
      flex: 1;
    }
    .sidebar {
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
    }
    .card-title {
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin-bottom: 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .telemetry-row {
      display: flex;
      justify-content: space-between;
      padding: 0.4rem 0;
      font-size: 0.875rem;
      border-bottom: 1px solid rgba(255,255,255,0.04);
    }
    .telemetry-row:last-child { border-bottom: none; }
    .telemetry-val {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 500;
      color: #93c5fd;
    }
    .chat-panel {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      height: calc(100vh - 130px);
    }
    .chat-history {
      flex: 1;
      overflow-y: auto;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .message {
      display: flex;
      flex-direction: column;
      max-width: 85%;
      padding: 0.9rem 1.15rem;
      border-radius: 12px;
      line-height: 1.55;
      font-size: 0.95rem;
    }
    .message.user {
      align-self: flex-end;
      background: #1e3a8a;
      color: #eff6ff;
      border-bottom-right-radius: 2px;
    }
    .message.assistant {
      align-self: flex-start;
      background: #181b26;
      border: 1px solid var(--border);
      color: var(--text);
      border-bottom-left-radius: 2px;
    }
    .message-meta {
      font-size: 0.75rem;
      color: var(--muted);
      margin-top: 0.4rem;
      font-family: 'JetBrains Mono', monospace;
    }
    .chat-input-area {
      padding: 1rem 1.5rem;
      border-top: 1px solid var(--border);
      background: #0f1118;
      display: flex;
      gap: 0.75rem;
    }
    textarea {
      flex: 1;
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      color: var(--text);
      font-family: inherit;
      font-size: 0.95rem;
      resize: none;
      height: 52px;
      outline: none;
      transition: border-color 0.2s;
    }
    textarea:focus { border-color: var(--accent); }
    button.btn-send {
      background: var(--accent);
      color: white;
      border: none;
      border-radius: 8px;
      padding: 0 1.5rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }
    button.btn-send:hover { background: #2563eb; }
    button.btn-send:disabled { background: #374151; cursor: not-allowed; }
    pre {
      background: var(--code-bg);
      padding: 0.75rem;
      border-radius: 6px;
      overflow-x: auto;
      margin: 0.5rem 0;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
    }
    code { font-family: 'JetBrains Mono', monospace; font-size: 0.9em; }
    .api-hint {
      font-size: 0.8rem;
      color: var(--muted);
      margin-top: 0.5rem;
      line-height: 1.4;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo">D</div>
      <div>
        <div class="brand-title">DeepSeek V4.1 Flash</div>
        <div style="font-size: 0.75rem; color: var(--muted);">KireiStudios Custom HIP Engine · Dual AMD Strix Halo</div>
      </div>
    </div>
    <div id="status-badge" class="badge-status">
      <div class="pulse-dot"></div>
      <span id="status-text">ONLINE</span>
    </div>
  </header>

  <div class="container">
    <div class="sidebar">
      <div class="card">
        <div class="card-title">Cluster Hardware</div>
        <div class="telemetry-row"><span>Arch</span><span class="telemetry-val">2× AMD gfx1151</span></div>
        <div class="telemetry-row"><span>Processori</span><span class="telemetry-val">Ryzen AI Max+ 395</span></div>
        <div class="telemetry-row"><span>Unified RAM</span><span class="telemetry-val">256 GB LPDDR5X</span></div>
        <div class="telemetry-row"><span>Interconnessione</span><span class="telemetry-val">USB4 PCIe Ring</span></div>
        <div class="telemetry-row"><span>RTT Link</span><span class="telemetry-val">~0.7 ms</span></div>
      </div>

      <div class="card">
        <div class="card-title">Specifiche Modello</div>
        <div class="telemetry-row"><span>Modello</span><span class="telemetry-val">DeepSeek-V4.1-Flash</span></div>
        <div class="telemetry-row"><span>Drafter Spec.</span><span class="telemetry-val">DSpark MXFP4</span></div>
        <div class="telemetry-row"><span>Dense Overlay</span><span class="telemetry-val">U6 + CB4</span></div>
        <div class="telemetry-row"><span>Max Context</span><span class="telemetry-val" id="model-ctx">12,288 tok</span></div>
        <div class="telemetry-row"><span>Target Decode</span><span class="telemetry-val" style="color:#10b981;">~50-70 tok/s</span></div>
      </div>

      <div class="card">
        <div class="card-title">OpenAI API Endpoint</div>
        <div class="api-hint">Endpoint base compatibile con Cursor, Cline, OpenWebUI e SDK:</div>
        <pre><code>https://yed1z.ai/v1</code></pre>
        <div class="api-hint">Modello da richiedere:</div>
        <pre><code>deepseek-v4.1-flash</code></pre>
      </div>
    </div>

    <div class="chat-panel">
      <div class="chat-history" id="chat-history">
        <div class="message assistant">
          Ciao! Il cluster Dual AMD Strix Halo è operativo e accelerato dal custom HIP engine di KireiStudios con DSpark speculative decoding. Come posso aiutarti?
        </div>
      </div>

      <div class="chat-input-area">
        <textarea id="prompt-input" placeholder="Scrivi un messaggio o una domanda sul codice..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
        <button id="send-btn" class="btn-send" onclick="sendMessage()">Invia</button>
      </div>
    </div>
  </div>

  <script>
    const messages = [];

    async function checkHealth() {
      try {
        const res = await fetch('/health');
        if (res.ok) {
          const data = await res.json();
          document.getElementById('status-text').textContent = 'ONLINE (' + data.model + ')';
        }
      } catch (e) {
        document.getElementById('status-badge').style.color = '#ef4444';
        document.getElementById('status-text').textContent = 'OFFLINE';
      }
    }
    checkHealth();
    setInterval(checkHealth, 15000);

    async function sendMessage() {
      const input = document.getElementById('prompt-input');
      const text = input.value.trim();
      if (!text) return;

      input.value = '';
      const history = document.getElementById('chat-history');

      const userDiv = document.createElement('div');
      userDiv.className = 'message user';
      userDiv.textContent = text;
      history.appendChild(userDiv);

      messages.push({ role: 'user', content: text });

      const assistantDiv = document.createElement('div');
      assistantDiv.className = 'message assistant';
      assistantDiv.textContent = '... generazione in corso ...';
      history.appendChild(assistantDiv);
      history.scrollTop = history.scrollHeight;

      const btn = document.getElementById('send-btn');
      btn.disabled = true;

      try {
        const t0 = performance.now();
        const res = await fetch('/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: 'deepseek-v4.1-flash',
            messages: messages,
            max_tokens: 1024,
            temperature: 0.1
          })
        });

        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        const t1 = performance.now();

        const content = data.choices[0].message.content;
        assistantDiv.textContent = content;

        const strix = data.strix || {};
        const tps = strix.decode_tokens_per_second ? strix.decode_tokens_per_second.toFixed(1) : ((data.usage.completion_tokens / ((t1-t0)/1000)).toFixed(1));
        
        const meta = document.createElement('div');
        meta.className = 'message-meta';
        meta.textContent = `${data.usage.completion_tokens} token · ${tps} tok/s · prefill: ${strix.prefill_ms ? strix.prefill_ms.toFixed(0)+'ms' : '-'}`;
        assistantDiv.appendChild(meta);

        messages.push({ role: 'assistant', content: content });
      } catch (err) {
        assistantDiv.textContent = 'Errore nella generazione: ' + err.message;
        assistantDiv.style.color = '#ef4444';
      } finally {
        btn.disabled = false;
        history.scrollTop = history.scrollHeight;
      }
    }
  </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HTML_PAGE.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_error(404, "Not Found")

def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"Web Dashboard listening on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

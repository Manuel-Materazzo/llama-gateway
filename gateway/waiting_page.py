"""HTML and UI templates for Onrender-style waiting page and services catalog."""

from __future__ import annotations

import html
import json
from typing import List, Optional
from gateway.config import WebServiceConfig


def render_waiting_page(
    service: WebServiceConfig,
    path_prefix: str,
    target_redirect_url: str,
    events_url: str,
    status_url: str,
    wake_url: str,
    max_timeout: float = 120.0,
) -> str:
    """
    Renders a modern, self-contained Onrender-style waiting page.
    Displays live wake status updates via SSE, pulsing progress animation,
    activity log console, countdown timer, and auto-redirects when healthy.
    """
    title = html.escape(service.title or service.name.capitalize())
    desc = html.escape(service.description or f"Web service '{service.name}' hosted on the private node.")
    icon = html.escape(service.icon or "⚡")
    safe_name = html.escape(service.name)
    mode = html.escape(service.mode)

    # Prepare JS parameters
    js_config = json.dumps({
        "serviceName": service.name,
        "mode": service.mode,
        "redirectUrl": target_redirect_url,
        "eventsUrl": events_url,
        "statusUrl": status_url,
        "wakeUrl": wake_url,
        "maxTimeout": max_timeout,
    })

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} &bull; Starting Server</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: rgba(17, 24, 39, 0.85);
      --border: rgba(255, 255, 255, 0.08);
      --border-focus: rgba(99, 102, 241, 0.4);
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #6366f1;
      --accent-grad: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
      --success: #10b981;
      --warning: #f59e0b;
      --error: #ef4444;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    body {{
      background-color: var(--bg);
      background-image: 
        radial-gradient(circle at 15% 20%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
        radial-gradient(circle at 85% 80%, rgba(236, 72, 153, 0.08) 0%, transparent 40%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px 16px;
    }}
    .badge-bar {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 20px;
    }}
    .badge-bar span.dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent);
    }}
    .container {{
      width: 100%;
      max-width: 580px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 36px 32px;
      backdrop-filter: blur(16px);
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
      position: relative;
      overflow: hidden;
    }}
    .glow-header {{
      display: flex;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 24px;
    }}
    .avatar {{
      width: 56px;
      height: 56px;
      border-radius: 14px;
      background: rgba(99, 102, 241, 0.15);
      border: 1px solid rgba(99, 102, 241, 0.3);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      flex-shrink: 0;
      box-shadow: 0 0 20px rgba(99, 102, 241, 0.2);
    }}
    .header-text {{
      flex: 1;
    }}
    .header-text h1 {{
      font-size: 24px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .header-text p {{
      font-size: 14px;
      color: var(--text-muted);
      margin-top: 4px;
      line-height: 1.4;
    }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      border-radius: 9999px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      font-size: 13px;
      font-weight: 500;
      margin-bottom: 24px;
    }}
    .status-pulse {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warning);
      box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7);
      animation: pulse 1.8s infinite;
    }}
    .status-pulse.ready {{
      background: var(--success);
      box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
      animation: pulse-ready 2s infinite;
    }}
    .status-pulse.error {{
      background: var(--error);
      box-shadow: none;
      animation: none;
    }}
    @keyframes pulse {{
      0% {{ box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.6); }}
      70% {{ box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }}
    }}
    @keyframes pulse-ready {{
      0% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6); }}
      70% {{ box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
    }}
    /* Progress bar */
    .progress-box {{
      margin-bottom: 24px;
    }}
    .progress-header {{
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }}
    .progress-track {{
      height: 8px;
      background: rgba(255, 255, 255, 0.06);
      border-radius: 9999px;
      overflow: hidden;
      position: relative;
    }}
    .progress-fill {{
      height: 100%;
      width: 15%;
      background: var(--accent-grad);
      border-radius: 9999px;
      transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative;
    }}
    .progress-fill::after {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.3), transparent);
      animation: shimmer 1.5s infinite;
    }}
    @keyframes shimmer {{
      0% {{ transform: translateX(-100%); }}
      100% {{ transform: translateX(100%); }}
    }}
    /* Stepper */
    .steps-list {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 24px;
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
    }}
    .step-item {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 13px;
      color: var(--text-muted);
      transition: color 0.3s;
    }}
    .step-item.active {{
      color: #fff;
      font-weight: 500;
    }}
    .step-item.done {{
      color: #34d399;
    }}
    .step-dot {{
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.1);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      color: var(--text-muted);
      flex-shrink: 0;
    }}
    .step-item.active .step-dot {{
      background: var(--accent);
      color: #fff;
    }}
    .step-item.done .step-dot {{
      background: rgba(16, 185, 129, 0.2);
      border: 1px solid var(--success);
      color: var(--success);
    }}
    /* Terminal Console */
    .console {{
      background: #06090e;
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 24px;
      overflow: hidden;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }}
    .console-header {{
      background: rgba(255, 255, 255, 0.03);
      padding: 8px 12px;
      display: flex;
      align-items: center;
      gap: 6px;
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .console-dots {{
      display: flex;
      gap: 4px;
      margin-right: 8px;
    }}
    .console-dots span {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.15);
    }}
    .console-body {{
      padding: 12px;
      height: 90px;
      overflow-y: auto;
      color: #a5b4fc;
      line-height: 1.6;
    }}
    .console-log {{
      margin-bottom: 4px;
      word-break: break-word;
    }}
    .console-time {{
      color: var(--text-muted);
      margin-right: 6px;
    }}
    /* Action Button */
    .actions {{
      display: flex;
      gap: 12px;
    }}
    .btn {{
      flex: 1;
      padding: 14px 20px;
      border-radius: 12px;
      border: none;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: all 0.2s ease;
      text-decoration: none;
    }}
    .btn-primary {{
      background: var(--accent-grad);
      color: #fff;
      box-shadow: 0 4px 15px rgba(99, 102, 241, 0.35);
    }}
    .btn-primary:hover:not(:disabled) {{
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5);
    }}
    .btn-primary:disabled {{
      opacity: 0.6;
      cursor: not-allowed;
      box-shadow: none;
    }}
    .btn-secondary {{
      background: rgba(255, 255, 255, 0.06);
      color: var(--text);
      border: 1px solid var(--border);
    }}
    .btn-secondary:hover {{
      background: rgba(255, 255, 255, 0.1);
    }}
    .footer-note {{
      margin-top: 20px;
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="badge-bar">
    <span class="dot"></span>
    <span>Llama Gateway &bull; Wake-on-Request</span>
  </div>

  <div class="container">
    <div class="glow-header">
      <div class="avatar">{icon}</div>
      <div class="header-text">
        <h1>{title}</h1>
        <p>{desc}</p>
      </div>
    </div>

    <div class="status-pill">
      <span class="status-pulse" id="pulseDot"></span>
      <span id="statusLabel">Waking up server...</span>
    </div>

    <div class="progress-box">
      <div class="progress-header">
        <span id="elapsedTimer">Elapsed: 00:00</span>
        <span id="progressPercent">20%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" id="progressBar"></div>
      </div>
    </div>

    <div class="steps-list">
      <div class="step-item done" id="step1">
        <div class="step-dot">&check;</div>
        <span>1. Wake command delivered to node</span>
      </div>
      <div class="step-item active" id="step2">
        <div class="step-dot">2</div>
        <span>2. Powering on physical machine &amp; OS</span>
      </div>
      <div class="step-item" id="step3">
        <div class="step-dot">3</div>
        <span>3. Initializing web service &amp; probing health</span>
      </div>
      <div class="step-item" id="step4">
        <div class="step-dot">4</div>
        <span>4. Online &bull; Ready for requests</span>
      </div>
    </div>

    <div class="console">
      <div class="console-header">
        <div class="console-dots">
          <span></span><span></span><span></span>
        </div>
        <span>Boot Sequence Log</span>
      </div>
      <div class="console-body" id="consoleBody">
        <div class="console-log"><span class="console-time">00:00</span>Incoming request detected; wake signal dispatched.</div>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-primary" id="launchBtn" disabled>
        <span id="launchBtnText">Waiting for server...</span>
      </button>
      <button class="btn btn-secondary" id="retryBtn" style="display: none;">
        Retry Wake-up
      </button>
    </div>
  </div>

  <div class="footer-note">
    Powered by Llama Gateway &bull; Zero-idle energy saving architecture
  </div>

  <script>
    const config = {js_config};
    const startTime = Date.now();
    let isReady = false;
    let timerInterval = null;
    let pollInterval = null;
    let eventSource = null;

    function formatTime(seconds) {{
      const mins = Math.floor(seconds / 60);
      const secs = Math.floor(seconds % 60);
      return `${{String(mins).padStart(2, '0')}}:${{String(secs).padStart(2, '0')}}`;
    }}

    function appendLog(msg) {{
      const body = document.getElementById("consoleBody");
      const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
      const row = document.createElement("div");
      row.className = "console-log";
      row.innerHTML = `<span class="console-time">${{formatTime(elapsedSec)}}</span>${{msg}}`;
      body.appendChild(row);
      body.scrollTop = body.scrollHeight;
    }}

    function setStep(stepNum) {{
      for (let i = 1; i <= 4; i++) {{
        const el = document.getElementById(`step${{i}}`);
        const dot = el.querySelector(".step-dot");
        if (i < stepNum) {{
          el.className = "step-item done";
          dot.innerHTML = "&check;";
        }} else if (i === stepNum) {{
          el.className = "step-item active";
          dot.textContent = i;
        }} else {{
          el.className = "step-item";
          dot.textContent = i;
        }}
      }}
    }}

    function updateProgress(percent, label) {{
      const bar = document.getElementById("progressBar");
      const pctLabel = document.getElementById("progressPercent");
      bar.style.width = `${{percent}}%`;
      pctLabel.textContent = `${{percent}}%`;
      if (label) {{
        document.getElementById("statusLabel").textContent = label;
      }}
    }}

    function onReady() {{
      if (isReady) return;
      isReady = true;
      clearInterval(timerInterval);
      clearInterval(pollInterval);
      if (eventSource) eventSource.close();

      setStep(4);
      updateProgress(100, "Service is online!");

      const pulseDot = document.getElementById("pulseDot");
      pulseDot.className = "status-pulse ready";

      appendLog("<strong>Health check successful! Redirecting to service...</strong>");

      const btn = document.getElementById("launchBtn");
      btn.disabled = false;
      let countdown = 2;
      btn.innerHTML = `Redirecting in ${{countdown}}s... 🚀`;

      const cdInterval = setInterval(() => {{
        countdown--;
        if (countdown <= 0) {{
          clearInterval(cdInterval);
          doRedirect();
        }} else {{
          btn.innerHTML = `Redirecting in ${{countdown}}s... 🚀`;
        }}
      }}, 1000);

      btn.onclick = () => {{
        clearInterval(cdInterval);
        doRedirect();
      }};
    }}

    function doRedirect() {{
      if (config.mode === "proxy") {{
        // Proxy mode: reload current page to hit the live service
        window.location.reload();
      }} else {{
        // Redirect mode: navigate directly to target URL
        window.location.href = config.redirectUrl;
      }}
    }}

    function onFailed(errMsg) {{
      clearInterval(timerInterval);
      clearInterval(pollInterval);
      if (eventSource) eventSource.close();

      const pulseDot = document.getElementById("pulseDot");
      pulseDot.className = "status-pulse error";
      document.getElementById("statusLabel").textContent = "Boot timeout";

      appendLog(`<span style="color: var(--error);">Error: ${{errMsg || "Server did not respond within timeout."}}</span>`);

      const btn = document.getElementById("launchBtn");
      btn.style.display = "none";
      const retryBtn = document.getElementById("retryBtn");
      retryBtn.style.display = "inline-flex";
      retryBtn.onclick = () => triggerRetry();
    }}

    async function triggerRetry() {{
      const retryBtn = document.getElementById("retryBtn");
      retryBtn.disabled = true;
      retryBtn.textContent = "Retrying...";
      try {{
        await fetch(config.wakeUrl, {{ method: "POST" }});
        window.location.reload();
      }} catch (err) {{
        appendLog(`Retry failed: ${{err.message}}`);
        retryBtn.disabled = false;
        retryBtn.textContent = "Retry Wake-up";
      }}
    }}

    // Real-time Timer
    timerInterval = setInterval(() => {{
      const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
      document.getElementById("elapsedTimer").textContent = `Elapsed: ${{formatTime(elapsedSec)}}`;

      if (elapsedSec > config.maxTimeout + 5 && !isReady) {{
        onFailed("Timed out waiting for server to respond.");
      }}
    }}, 1000);

    // Connect to SSE event stream
    try {{
      eventSource = new EventSource(config.eventsUrl);

      eventSource.onmessage = (e) => {{
        try {{
          const data = JSON.parse(e.data);
          if (data.healthy || data.phase === "ready") {{
            onReady();
          }} else if (data.phase === "checking") {{
            setStep(3);
            updateProgress(75, "Probing service health...");
            appendLog(data.message || "Probing health endpoint...");
          }} else if (data.phase === "booting") {{
            setStep(2);
            updateProgress(40, "Node booting...");
            appendLog(data.message || "Waiting for machine to boot...");
          }} else if (data.phase === "failed") {{
            onFailed(data.message);
          }}
        }} catch (parseErr) {{
          console.error("SSE parse error", parseErr);
        }}
      }};

      eventSource.onerror = () => {{
        // Fallback to polling if SSE is disconnected
        if (!isReady && !pollInterval) {{
          startPolling();
        }}
      }};
    }} catch (err) {{
      startPolling();
    }}

    // Fallback polling
    function startPolling() {{
      pollInterval = setInterval(async () => {{
        try {{
          const res = await fetch(config.statusUrl);
          if (res.ok) {{
            const st = await res.json();
            if (st.healthy) {{
              onReady();
            }}
          }}
        }} catch (e) {{
          // Still waiting
        }}
      }}, 2500);
    }}
  </script>
</body>
</html>
"""


def render_services_catalog(services: List[WebServiceConfig], base_path: str = "/services") -> str:
    """Renders a dashboard catalog listing all configured web & API services."""
    cards_html = []
    for svc in services:
        name = html.escape(svc.name)
        title = html.escape(svc.title or svc.name.capitalize())
        desc = html.escape(svc.description or f"Service hosted at {svc.backend_url}")
        icon = html.escape(svc.icon or "⚡")
        path = svc.effective_path_prefix
        mode = html.escape(svc.mode)

        cards_html.append(f"""
        <div class="svc-card">
          <div class="svc-header">
            <div class="svc-icon">{icon}</div>
            <div class="svc-title-box">
              <h3>{title}</h3>
              <span class="svc-mode">{mode.upper()}</span>
            </div>
          </div>
          <p class="svc-desc">{desc}</p>
          <div class="svc-footer">
            <span class="svc-path"><code>{path}</code></span>
            <a href="{path}" class="svc-launch-btn">Open &rarr;</a>
          </div>
        </div>
        """)

    cards_joined = "\n".join(cards_html) if cards_html else "<p style='color: #9ca3af;'>No services currently configured in config.yaml.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Llama Gateway &bull; Services Catalog</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: rgba(17, 24, 39, 0.85);
      --border: rgba(255, 255, 255, 0.08);
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #6366f1;
      --accent-grad: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 40px 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .catalog-container {{
      width: 100%;
      max-width: 900px;
    }}
    header {{
      margin-bottom: 36px;
      text-align: center;
    }}
    header h1 {{
      font-size: 32px;
      font-weight: 700;
      background: var(--accent-grad);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 8px;
    }}
    header p {{
      color: var(--text-muted);
      font-size: 15px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 20px;
    }}
    .svc-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      backdrop-filter: blur(12px);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.2s, border-color 0.2s;
    }}
    .svc-card:hover {{
      transform: translateY(-2px);
      border-color: rgba(99, 102, 241, 0.4);
    }}
    .svc-header {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 12px;
    }}
    .svc-icon {{
      font-size: 26px;
      width: 48px;
      height: 48px;
      border-radius: 12px;
      background: rgba(99, 102, 241, 0.15);
      border: 1px solid rgba(99, 102, 241, 0.3);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .svc-title-box h3 {{
      font-size: 18px;
      color: #fff;
    }}
    .svc-mode {{
      font-size: 10px;
      letter-spacing: 0.05em;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-muted);
    }}
    .svc-desc {{
      color: var(--text-muted);
      font-size: 13px;
      line-height: 1.5;
      margin-bottom: 20px;
      flex: 1;
    }}
    .svc-footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .svc-path code {{
      font-size: 11px;
      color: #a5b4fc;
      background: rgba(0, 0, 0, 0.3);
      padding: 3px 6px;
      border-radius: 4px;
    }}
    .svc-launch-btn {{
      background: var(--accent);
      color: #fff;
      padding: 6px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      transition: opacity 0.2s;
    }}
    .svc-launch-btn:hover {{
      opacity: 0.9;
    }}
  </style>
</head>
<body>
  <div class="catalog-container">
    <header>
      <h1>Services Catalog</h1>
      <p>Self-hosted web applications and API services managed with wake-on-request.</p>
    </header>
    <div class="grid">
      {cards_joined}
    </div>
  </div>
</body>
</html>
"""

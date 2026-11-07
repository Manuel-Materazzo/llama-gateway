"""HTML and UI templates for Onrender-style waiting page and services catalog."""

from __future__ import annotations

import html
import json
import urllib.parse
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
    Renders a modern, self-contained Onrender-style waiting page based on the
    Obsidian Forge design system. Displays live wake status updates via SSE,
    animated stepper progress, activity log console, countdown timer, and
    auto-redirects when healthy.
    """
    title = html.escape(service.title or service.name.capitalize())
    desc = html.escape(service.description or f"Web service '{service.name}' hosted on the private node.")
    icon = html.escape(service.icon or "🤖")
    safe_name = html.escape(service.name)
    mode = html.escape(service.mode)
    is_local = not service.should_wake_node

    # Node badge & target port info
    parsed = urllib.parse.urlsplit(service.backend_url)
    hostname = parsed.hostname or ("localhost" if is_local else "REMOTE-NODE")
    node_badge = html.escape(f"NODE: {hostname.upper()}")
    if parsed.port:
        port_display = f":{parsed.port}"
    elif parsed.scheme == "https":
        port_display = ":443"
    else:
        port_display = ":80"

    # Estimated boot time display
    est_sec = 15 if is_local else min(int(max_timeout * 0.4), 90)
    est_mins = est_sec // 60
    est_rem = est_sec % 60
    est_display = f"{est_mins:02d}:{est_rem:02d}"

    if is_local:
        step1_text = "1. Service startup signal dispatched"
        step2_text = "2. Starting local container / service"
        step3_text = "3. Probing health check endpoint"
        status_label = "Starting local service (Stage 2/4)"
        initial_log = f"Received incoming HTTP traffic for {safe_name}. Starting local service."
        boot_log_title = "Boot Sequence Log &amp; Telemetry"
        footer_note = "Local containerized service"
    else:
        step1_text = "1. Wake packet delivered to target node"
        step2_text = "2. Hardware power-on &amp; OS kernel load"
        step3_text = "3. Initializing web service &amp; probing health"
        status_label = "Boot in progress (Stage 2/4)"
        initial_log = f"Received incoming HTTP traffic for {safe_name}. Remote wake signal dispatched."
        boot_log_title = "Boot Sequence Log &amp; Telemetry"
        footer_note = "Zero-idle energy saving architecture"

    # Prepare JS configuration object
    js_config = json.dumps({
        "serviceName": service.name,
        "mode": service.mode,
        "isLocal": is_local,
        "redirectUrl": target_redirect_url,
        "eventsUrl": events_url,
        "statusUrl": status_url,
        "wakeUrl": wake_url,
        "maxTimeout": max_timeout,
        "healthPath": service.health_path,
    })

    return f"""<!DOCTYPE html>
<html class="dark" lang="en">
<head>
  <meta charset="utf-8">
  <meta content="width=device-width, initial-scale=1.0" name="viewport">
  <title>{title} &bull; Starting Server &bull; Cold-Start Boot Environment | Llama Gateway</title>
  <!-- Tailwind CSS CDN with Forms & Container Queries -->
  <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
  <!-- Tailwind Configuration -->
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            zinc: {{
              850: '#1f1f23',
              900: '#18181b',
              950: '#09090b',
            }}
          }},
          fontFamily: {{
            mono: ['JetBrains Mono', 'Fira Code', 'Menlo', 'Consolas', 'monospace'],
            sans: ['Geist', 'Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
          }}
        }}
      }}
    }};
  </script>
  <!-- Google Fonts: Inter, Geist & JetBrains Mono -->
  <link href="https://fonts.googleapis.com" rel="preconnect">
  <link crossorigin="" href="https://fonts.gstatic.com" rel="preconnect">
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&amp;family=Inter:wght@300;400;500;600;700&amp;family=JetBrains+Mono:wght@400;500;600&amp;display=swap" rel="stylesheet">
  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@latest"></script>
  <!-- Custom Core Styles -->
  <style data-purpose="background-effects">
    body {{
      background-color: #09090b;
      font-family: 'Geist', 'Inter', sans-serif;
      overflow-x: hidden;
    }}
    .grid-backdrop {{
      background-size: 32px 32px;
      background-image: 
        linear-gradient(to right, rgba(255, 255, 255, 0.02) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
    }}
  </style>
  <style data-purpose="terminal-scroll">
    /* Sleek custom scrollbars for code log */
    ::-webkit-scrollbar {{
      width: 5px;
      height: 5px;
    }}
    ::-webkit-scrollbar-track {{
      background: #09090b;
    }}
    ::-webkit-scrollbar-thumb {{
      background: #27272a;
      border-radius: 4px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
      background: #3f3f46;
    }}
  </style>
</head>
<body class="min-h-screen text-zinc-300 antialiased flex flex-col justify-between relative selection:bg-zinc-800 selection:text-white">
  <!-- Ambient Backdrop -->
  <div aria-hidden="true" class="fixed inset-0 pointer-events-none z-0 overflow-hidden">
    <div class="absolute inset-0 grid-backdrop opacity-100"></div>
  </div>

  <!-- TopBar -->
  <header class="relative z-10 w-full pt-8 pb-4 flex flex-col items-center justify-center px-4">
    <div class="inline-flex items-center gap-2.5 px-3 py-1 rounded-md border border-zinc-800 bg-zinc-900/90 text-xs font-mono tracking-tight text-zinc-400">
      <span class="flex h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
      <span class="font-medium text-zinc-200">LLAMA GATEWAY</span>
      <span class="text-zinc-600 font-mono">/</span>
      <span class="text-zinc-400">WAKE-ON-REQUEST</span>
      <span class="text-zinc-600 font-mono">/</span>
      <span class="text-zinc-300 bg-zinc-800/80 px-1.5 py-0.5 rounded border border-zinc-700/60 text-[11px]">{node_badge}</span>
      <span class="text-zinc-600 font-mono">/</span>
      <span class="text-emerald-400 flex items-center gap-1 font-mono text-[11px]">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 inline"><path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"></path></svg>
        <span id="headerLatency">LIVE</span>
      </span>
    </div>
  </header>

  <!-- Main Content Card -->
  <main class="relative z-10 w-full max-w-4xl mx-auto px-4 py-4 flex-grow flex flex-col items-center justify-center">
    <div class="w-full relative rounded-xl border border-zinc-800 bg-zinc-900 shadow-xl overflow-hidden">
      <div class="w-full p-6 sm:p-8 relative">
        <!-- Instance Header Section -->
        <section class="flex flex-col sm:flex-row sm:items-center justify-between gap-5 border-b border-zinc-800/80 pb-6" data-purpose="service-meta-header">
          <div class="flex items-start gap-4">
            <!-- App Logo Badge -->
            <div class="relative shrink-0">
              <div class="w-12 h-12 rounded-lg bg-zinc-950 border border-zinc-800 flex items-center justify-center text-2xl text-zinc-200">
                {icon}
              </div>
            </div>
            <!-- Title & Description -->
            <div>
              <div class="flex items-center gap-2.5 flex-wrap">
                <h1 class="text-xl font-semibold tracking-tight text-white flex items-center gap-2">
                  {title}
                </h1>
                <span class="text-xs font-mono text-zinc-400 bg-zinc-950 px-2 py-0.5 rounded border border-zinc-800 uppercase">{mode}</span>
              </div>
              <p class="text-xs text-zinc-400 mt-1">
                {desc}
              </p>
            </div>
          </div>
          <!-- Live Status Indicator Pill -->
          <div class="flex items-center sm:flex-col sm:items-end gap-1.5 shrink-0">
            <div id="statusPill" class="inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-amber-500/10 border border-amber-500/20 text-amber-400 text-xs font-medium">
              <span id="pulseDot" class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
              <span id="statusLabel">{status_label}</span>
            </div>
            <span class="text-xs font-mono text-zinc-500">Target Port: <span class="text-zinc-300 font-medium">{port_display}</span></span>
          </div>
        </section>

        <!-- Progress and Timer Section -->
        <section class="mt-6 space-y-2" data-purpose="boot-progress-tracker">
          <div class="flex justify-between items-center text-xs">
            <div class="flex items-center gap-2">
              <span class="text-zinc-500">Boot Elapsed:</span>
              <span class="font-mono font-medium text-zinc-200 bg-zinc-950 px-2 py-0.5 rounded border border-zinc-800" id="elapsedTimer">00:00</span>
              <span class="text-zinc-500 font-mono">/ est. {est_display}</span>
            </div>
            <div class="flex items-center gap-1.5 font-mono text-xs">
              <span class="text-zinc-500">Progress:</span>
              <span class="font-medium text-zinc-200" id="progressPercent">25%</span>
            </div>
          </div>
          <!-- Utilitarian Progress Bar -->
          <div class="relative w-full h-2 rounded bg-zinc-950 border border-zinc-800 overflow-hidden">
            <div id="progressBar" class="h-full bg-zinc-200 w-[25%] transition-all duration-700 ease-out"></div>
          </div>
        </section>

        <!-- Boot Stages Stepper -->
        <section class="mt-6 bg-zinc-950/70 rounded-lg p-3 sm:p-4 border border-zinc-800/80" data-purpose="stepper-stages">
          <ul class="space-y-2.5" id="stepperList">
            <!-- Step 1: Completed -->
            <li id="step1" class="flex items-center justify-between text-xs sm:text-sm py-1">
              <div class="flex items-center gap-3">
                <span class="step-icon w-5 h-5 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 stroke-[2.5]"><path d="M20 6 9 17l-5-5"></path></svg>
                </span>
                <span class="step-text text-zinc-300 font-medium">{step1_text}</span>
              </div>
              <div class="step-meta flex items-center gap-2.5">
                <span class="step-time text-xs font-mono text-zinc-400 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">+0.04s</span>
                <span class="step-status text-xs text-zinc-500">Done</span>
              </div>
            </li>
            <!-- Step 2: Active Stage -->
            <li id="step2" class="flex items-center justify-between text-xs sm:text-sm -mx-2 px-2 py-1.5 rounded-md bg-zinc-900 border border-zinc-800">
              <div class="flex items-center gap-3">
                <span class="step-icon w-5 h-5 rounded-full bg-amber-500/10 border border-amber-500/40 flex items-center justify-center text-amber-400 shrink-0">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 animate-spin"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
                </span>
                <span class="step-text text-zinc-100 font-medium flex items-center gap-2 flex-wrap">
                  {step2_text}
                </span>
              </div>
              <div class="step-meta flex items-center gap-2">
                <span class="inline-flex items-center gap-1.5 text-xs font-mono text-amber-400">
                  <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
                  Active
                </span>
              </div>
            </li>
            <!-- Step 3: Pending -->
            <li id="step3" class="flex items-center justify-between text-xs sm:text-sm py-1 opacity-50">
              <div class="flex items-center gap-3">
                <span class="step-icon w-5 h-5 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-500 text-[11px] font-mono shrink-0">
                  3
                </span>
                <span class="step-text text-zinc-400">{step3_text}</span>
              </div>
              <div class="step-meta">
                <span class="step-status text-xs font-mono text-zinc-600 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">Awaiting ready</span>
              </div>
            </li>
            <!-- Step 4: Pending -->
            <li id="step4" class="flex items-center justify-between text-xs sm:text-sm py-1 opacity-50">
              <div class="flex items-center gap-3">
                <span class="step-icon w-5 h-5 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-500 text-[11px] font-mono shrink-0">
                  4
                </span>
                <span class="step-text text-zinc-400">4. Service online &amp; ready for traffic</span>
              </div>
              <div class="step-meta">
                <span class="step-status text-xs font-mono text-zinc-600 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">Awaiting ready</span>
              </div>
            </li>
          </ul>
        </section>

        <!-- Live Console Diagnostics -->
        <section class="mt-5 rounded-lg border border-zinc-800 bg-zinc-950 overflow-hidden" data-purpose="live-console">
          <!-- Terminal Header Bar -->
          <div class="bg-zinc-900/90 px-3.5 py-2 border-b border-zinc-800 flex items-center justify-between text-xs">
            <div class="flex items-center gap-2">
              <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-full bg-zinc-700 inline-block"></span>
                <span class="w-2.5 h-2.5 rounded-full bg-zinc-700 inline-block"></span>
                <span class="w-2.5 h-2.5 rounded-full bg-zinc-700 inline-block"></span>
              </div>
              <span class="font-mono text-zinc-400 ml-2 text-[11px] font-medium uppercase tracking-wider">{boot_log_title}</span>
            </div>
            <!-- Terminal Actions -->
            <div class="flex items-center gap-2 text-zinc-400 font-mono text-[11px]">
              <button id="copyLogsBtn" onclick="copyConsoleLogs()" class="hover:text-zinc-200 transition-colors flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-zinc-800 text-zinc-400">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>
                <span id="copyBtnText">Copy</span>
              </button>
              <span class="text-zinc-700">|</span>
              <span id="streamStatusBadge" class="text-zinc-400 flex items-center gap-1.5 font-mono text-[11px]">
                <span id="streamDot" class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span id="streamText">STREAMING</span>
              </span>
            </div>
          </div>
          <!-- Console Terminal Output Body -->
          <div id="consoleBody" class="p-3.5 font-mono text-[11px] leading-relaxed max-h-48 overflow-y-auto space-y-1 text-zinc-300">
            <div class="text-zinc-500">[00:00.01] <span class="text-zinc-400 font-medium">INFO</span> [Gateway] {initial_log}</div>
            <div id="consoleCursor" class="flex items-center gap-1 text-zinc-500 animate-pulse pt-0.5">
              <span>_</span>
            </div>
          </div>
        </section>

        <!-- Action Controls Bar -->
        <section class="mt-6 pt-5 border-t border-zinc-800/80 flex flex-col sm:flex-row items-center justify-between gap-4" data-purpose="action-controls">
          <!-- Auto-Redirect Toggle -->
          <label class="flex items-center gap-2.5 cursor-pointer text-xs text-zinc-400 hover:text-zinc-200 transition-colors select-none">
            <input type="checkbox" id="autoRedirectCheck" checked class="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-0 focus:ring-offset-0 w-3.5 h-3.5">
            <span class="flex items-center gap-1.5">
              Auto-redirect when online
            </span>
          </label>
          <!-- Interaction Buttons -->
          <div class="flex items-center gap-2.5 w-full sm:w-auto justify-end">
            <!-- Cancel / Abort button -->
            <button onclick="handleCancelRequest()" class="px-3 py-1.5 rounded-md text-xs font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-all border border-transparent">
              Cancel Request
            </button>
            <!-- Force Retry Wake-up Primary Button -->
            <button id="retryBtn" onclick="triggerRetry()" style="display: none;" class="px-3.5 py-1.5 rounded-md text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 flex items-center gap-1.5 transition-all active:scale-[0.98]">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"></path><path d="M21 3v5h-5"></path></svg>
              <span>Retry Wake-Up</span>
            </button>
            <!-- Launch button -->
            <button id="launchBtn" disabled class="px-3.5 py-1.5 rounded-md text-xs font-medium bg-zinc-100 hover:bg-white text-zinc-900 border border-zinc-200 flex items-center gap-1.5 transition-all active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed">
              <span id="launchBtnText">Waiting for server...</span>
            </button>
          </div>
        </section>
      </div>
    </div>
  </main>

  <!-- Footer -->
  <footer class="relative z-10 w-full py-6 text-center text-xs text-zinc-500 font-mono space-y-1">
    <p>Powered by <span class="text-zinc-400 font-medium">Llama Gateway</span> &bull; {footer_note} &bull; Max timeout: {int(max_timeout)}s</p>
    <p class="text-[11px] text-zinc-600">Enterprise AI Infrastructure &bull; All nodes verified cold-boot compliant &bull; ISO 27001</p>
  </footer>

  <!-- Scripts -->
  <script>
    const config = {js_config};
    const startTime = Date.now();
    let isReady = false;
    let timerInterval = null;
    let pollInterval = null;
    let eventSource = null;
    let countdownInterval = null;
    let currentStep = 2;

    const stepTimes = {{
      1: "+0.04s",
      2: null,
      3: null,
      4: null,
    }};

    const step1BaseText = {json.dumps(step1_text)};
    const step2BaseText = {json.dumps(step2_text)};
    const step3BaseText = {json.dumps(step3_text)};
    const step4BaseText = "4. Service online & ready for traffic";

    const stepLabels = [
      "",
      step1BaseText,
      step2BaseText,
      step3BaseText,
      step4BaseText
    ];

    const checkSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 stroke-[2.5]"><path d="M20 6 9 17l-5-5"></path></svg>';
    const spinnerSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 animate-spin"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>';

    function htmlEscape(str) {{
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }}

    function formatClockTime(seconds) {{
      const mins = String(Math.floor(seconds / 60)).padStart(2, '0');
      const secs = String(Math.floor(seconds % 60)).padStart(2, '0');
      return `${{mins}}:${{secs}}`;
    }}

    function formatLogTime(ms) {{
      const totalSec = ms / 1000;
      const mins = String(Math.floor(totalSec / 60)).padStart(2, '0');
      const secs = String(Math.floor(totalSec % 60)).padStart(2, '0');
      const hundredths = String(Math.floor((ms % 1000) / 10)).padStart(2, '0');
      return `${{mins}}:${{secs}}.${{hundredths}}`;
    }}

    function appendLog(msg, level = "INFO", tag = "Gateway") {{
      const body = document.getElementById("consoleBody");
      const cursor = document.getElementById("consoleCursor");
      if (!body) return;

      const elapsedMs = Math.max(10, Date.now() - startTime);
      const timeStr = formatLogTime(elapsedMs);

      let badgeClass = "text-zinc-400";
      if (level === "PROBE") badgeClass = "text-amber-400";
      else if (level === "EXEC") badgeClass = "text-emerald-400/90";
      else if (level === "SUCCESS") badgeClass = "text-emerald-400";
      else if (level === "ERR") badgeClass = "text-red-400";

      const row = document.createElement("div");
      row.className = "text-zinc-300";
      row.innerHTML = `<span class="text-zinc-500">[${{timeStr}}]</span> <span class="${{badgeClass}} font-medium">${{level}}</span> <span class="text-zinc-400">[${{tag}}]</span> ${{htmlEscape(msg)}}`;

      if (cursor && cursor.parentNode === body) {{
        body.insertBefore(row, cursor);
      }} else {{
        body.appendChild(row);
      }}
      body.scrollTop = body.scrollHeight;
    }}

    function setStep(stepNum, extraBadgeText) {{
      currentStep = stepNum;
      for (let i = 1; i <= 4; i++) {{
        const item = document.getElementById(`step${{i}}`);
        if (!item) continue;
        const iconSpan = item.querySelector(".step-icon");
        const textSpan = item.querySelector(".step-text");
        const metaDiv = item.querySelector(".step-meta");

        if (i < stepNum) {{
          // Completed
          item.className = "flex items-center justify-between text-xs sm:text-sm py-1 opacity-100";
          if (iconSpan) {{
            iconSpan.className = "step-icon w-5 h-5 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0";
            iconSpan.innerHTML = checkSvg;
          }}
          if (textSpan) {{
            textSpan.className = "step-text text-zinc-300 font-medium";
            textSpan.innerHTML = stepLabels[i];
          }}
          if (metaDiv) {{
            if (!stepTimes[i]) {{
              const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(2);
              stepTimes[i] = `+${{elapsedSec}}s`;
            }}
            metaDiv.className = "step-meta flex items-center gap-2.5";
            metaDiv.innerHTML = `<span class="step-time text-xs font-mono text-zinc-400 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">${{stepTimes[i]}}</span><span class="step-status text-xs text-zinc-500">Done</span>`;
          }}
        }} else if (i === stepNum) {{
          // Active
          item.className = "flex items-center justify-between text-xs sm:text-sm -mx-2 px-2 py-1.5 rounded-md bg-zinc-900 border border-zinc-800 opacity-100";
          if (iconSpan) {{
            iconSpan.className = "step-icon w-5 h-5 rounded-full bg-amber-500/10 border border-amber-500/40 flex items-center justify-center text-amber-400 shrink-0";
            iconSpan.innerHTML = spinnerSvg;
          }}
          if (textSpan) {{
            textSpan.className = "step-text text-zinc-100 font-medium flex items-center gap-2 flex-wrap";
            let extra = "";
            if (extraBadgeText) {{
              extra = ` <span class="hidden sm:inline text-[11px] font-mono font-normal text-zinc-400 border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 rounded">${{htmlEscape(extraBadgeText)}}</span>`;
            }}
            textSpan.innerHTML = stepLabels[i] + extra;
          }}
          if (metaDiv) {{
            metaDiv.className = "step-meta flex items-center gap-2";
            metaDiv.innerHTML = `<span class="inline-flex items-center gap-1.5 text-xs font-mono text-amber-400"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>Active</span>`;
          }}
        }} else {{
          // Pending
          item.className = "flex items-center justify-between text-xs sm:text-sm py-1 opacity-50";
          if (iconSpan) {{
            iconSpan.className = "step-icon w-5 h-5 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-500 text-[11px] font-mono shrink-0";
            iconSpan.textContent = i;
          }}
          if (textSpan) {{
            textSpan.className = "step-text text-zinc-400";
            textSpan.innerHTML = stepLabels[i];
          }}
          if (metaDiv) {{
            metaDiv.className = "step-meta";
            metaDiv.innerHTML = `<span class="step-status text-xs font-mono text-zinc-600 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">Awaiting ready</span>`;
          }}
        }}
      }}
    }}

    function updateProgress(percent, label) {{
      const bar = document.getElementById("progressBar");
      const pctLabel = document.getElementById("progressPercent");
      if (bar) bar.style.width = `${{percent}}%`;
      if (pctLabel) pctLabel.textContent = `${{percent}}%`;
      if (label) {{
        const lbl = document.getElementById("statusLabel");
        if (lbl) lbl.textContent = label;
      }}
    }}

    function onReady() {{
      if (isReady) return;
      isReady = true;
      clearInterval(timerInterval);
      clearInterval(pollInterval);
      if (eventSource) eventSource.close();

      setStep(4);
      // Mark step 4 Done
      const step4 = document.getElementById("step4");
      if (step4) {{
        const iconSpan = step4.querySelector(".step-icon");
        const metaDiv = step4.querySelector(".step-meta");
        if (iconSpan) {{
          iconSpan.className = "step-icon w-5 h-5 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0";
          iconSpan.innerHTML = checkSvg;
        }}
        if (metaDiv) {{
          const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(2);
          metaDiv.className = "step-meta flex items-center gap-2.5";
          metaDiv.innerHTML = `<span class="step-time text-xs font-mono text-zinc-400 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">+${{elapsedSec}}s</span><span class="step-status text-xs text-zinc-500">Done</span>`;
        }}
      }}

      updateProgress(100, "Service is online!");

      const pill = document.getElementById("statusPill");
      if (pill) {{
        pill.className = "inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-medium";
      }}
      const dot = document.getElementById("pulseDot");
      if (dot) {{
        dot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400";
      }}

      const streamDot = document.getElementById("streamDot");
      if (streamDot) streamDot.className = "w-1.5 h-1.5 rounded-full bg-emerald-500";
      const streamText = document.getElementById("streamText");
      if (streamText) streamText.textContent = "READY";

      appendLog("Health check successful! Service is reachable.", "SUCCESS", "Health");

      const btn = document.getElementById("launchBtn");
      btn.disabled = false;
      btn.classList.remove("disabled:opacity-50", "disabled:cursor-not-allowed");

      const autoRedirect = document.getElementById("autoRedirectCheck")?.checked ?? true;

      if (autoRedirect) {{
        let countdown = 2;
        btn.innerHTML = `<span>Redirecting in ${{countdown}}s...</span> <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>`;

        countdownInterval = setInterval(() => {{
          countdown--;
          if (countdown <= 0) {{
            clearInterval(countdownInterval);
            doRedirect();
          }} else {{
            btn.innerHTML = `<span>Redirecting in ${{countdown}}s...</span> <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>`;
          }}
        }}, 1000);
      }} else {{
        btn.innerHTML = `<span>Launch Service</span> <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>`;
      }}

      btn.onclick = () => {{
        if (countdownInterval) clearInterval(countdownInterval);
        doRedirect();
      }};
    }}

    function doRedirect() {{
      if (config.mode === "proxy") {{
        window.location.reload();
      }} else {{
        window.location.href = config.redirectUrl;
      }}
    }}

    function onFailed(errMsg) {{
      clearInterval(timerInterval);
      clearInterval(pollInterval);
      if (eventSource) eventSource.close();

      const pill = document.getElementById("statusPill");
      if (pill) {{
        pill.className = "inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-red-500/10 border border-red-500/20 text-red-400 text-xs font-medium";
      }}
      const dot = document.getElementById("pulseDot");
      if (dot) {{
        dot.className = "w-1.5 h-1.5 rounded-full bg-red-400";
      }}
      const statusLabel = document.getElementById("statusLabel");
      if (statusLabel) {{
        statusLabel.textContent = "Boot timeout / Failed";
      }}

      const streamDot = document.getElementById("streamDot");
      if (streamDot) streamDot.className = "w-1.5 h-1.5 rounded-full bg-red-500";
      const streamText = document.getElementById("streamText");
      if (streamText) streamText.textContent = "FAILED";

      appendLog(`Error: ${{errMsg || "Server did not respond within timeout limit."}}`, "ERR", "Gateway");

      const launchBtn = document.getElementById("launchBtn");
      if (launchBtn) launchBtn.style.display = "none";

      const retryBtn = document.getElementById("retryBtn");
      if (retryBtn) retryBtn.style.display = "inline-flex";
    }}

    async function triggerRetry() {{
      const retryBtn = document.getElementById("retryBtn");
      if (retryBtn) {{
        retryBtn.disabled = true;
        retryBtn.classList.add("opacity-50");
      }}
      appendLog("Manual wake-up retry initiated...", "INFO", "Gateway");
      try {{
        const res = await fetch(config.wakeUrl, {{ method: "POST" }});
        if (res.ok) {{
          appendLog("Wake command re-sent successfully. Reloading environment...", "SUCCESS", "WakeDaemon");
          setTimeout(() => {{ window.location.reload(); }}, 1000);
        }} else {{
          throw new Error(`HTTP ${{res.status}}`);
        }}
      }} catch (err) {{
        appendLog(`Retry failed: ${{err.message}}`, "ERR", "Gateway");
        if (retryBtn) {{
          retryBtn.disabled = false;
          retryBtn.classList.remove("opacity-50");
        }}
      }}
    }}

    function handleCancelRequest() {{
      if (window.history.length > 1) {{
        window.history.back();
      }} else {{
        window.location.href = "/services";
      }}
    }}

    function copyConsoleLogs() {{
      const body = document.getElementById("consoleBody");
      if (!body) return;
      const rows = Array.from(body.children).filter(el => el.id !== "consoleCursor");
      const text = rows.map(r => r.innerText.trim()).join("\\n");
      navigator.clipboard.writeText(text).then(() => {{
        const textSpan = document.getElementById("copyBtnText");
        if (textSpan) {{
          const original = textSpan.textContent;
          textSpan.textContent = "Copied!";
          setTimeout(() => {{ textSpan.textContent = original; }}, 2000);
        }}
      }}).catch(err => {{
        console.warn("Clipboard copy failed", err);
      }});
    }}

    async function measureLatency() {{
      const startPing = performance.now();
      try {{
        await fetch(config.statusUrl, {{ method: "GET", cache: "no-store" }});
        const latencyMs = Math.max(1, Math.round(performance.now() - startPing));
        const latEl = document.getElementById("headerLatency");
        if (latEl) latEl.textContent = `${{latencyMs}}ms`;
      }} catch (e) {{
        // ignore
      }}
    }}

    // Real-time Timer
    timerInterval = setInterval(() => {{
      const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
      const timerEl = document.getElementById("elapsedTimer");
      if (timerEl) {{
        timerEl.textContent = formatClockTime(elapsedSec);
      }}

      if (elapsedSec > config.maxTimeout + 5 && !isReady) {{
        onFailed("Timed out waiting for server to respond.");
      }}
    }}, 1000);

    // Initial latency measure and periodic interval
    measureLatency();
    setInterval(measureLatency, 5000);

    // Auto-redirect toggle handler
    const autoCheck = document.getElementById("autoRedirectCheck");
    if (autoCheck) {{
      autoCheck.addEventListener("change", (e) => {{
        if (!e.target.checked && countdownInterval) {{
          clearInterval(countdownInterval);
          const btn = document.getElementById("launchBtn");
          if (btn && isReady) {{
            btn.innerHTML = `<span>Launch Service</span> <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>`;
          }}
        }}
      }});
    }}

    // Connect to SSE event stream
    try {{
      eventSource = new EventSource(config.eventsUrl);

      eventSource.onmessage = (e) => {{
        try {{
          const data = JSON.parse(e.data);
          if (data.healthy || data.phase === "ready") {{
            onReady();
          }} else if (data.phase === "checking") {{
            setStep(3, `GET ${{config.healthPath || "/health"}}`);
            updateProgress(85, "Probing service health (Stage 3/4)");
            appendLog(data.message || "Probing health endpoint...", "PROBE", "Health");
          }} else if (data.phase === "starting_service") {{
            setStep(3);
            updateProgress(70, "Starting service (Stage 3/4)");
            appendLog(data.message || "Dispatching service startup command...", "EXEC", "Service");
          }} else if (data.phase === "server_ready") {{
            setStep(2);
            updateProgress(55, "Server machine online (Stage 2/4)");
            appendLog(data.message || "Server machine is online!", "SUCCESS", "Host");
          }} else if (data.phase === "booting") {{
            setStep(2);
            updateProgress(35, "Node booting (Stage 2/4)");
            appendLog(data.message || "Waiting for machine to boot...", "INFO", "Node");
          }} else if (data.phase === "waking") {{
            setStep(1);
            updateProgress(20, "Initiating wake (Stage 1/4)");
            appendLog(data.message || "Sending wake command to target node...", "INFO", "WakeDaemon");
          }} else if (data.phase === "failed") {{
            onFailed(data.message);
          }}
        }} catch (parseErr) {{
          console.error("SSE parse error", parseErr);
        }}
      }};

      eventSource.onerror = () => {{
        if (!isReady && !pollInterval) {{
          startPolling();
        }}
      }};
    }} catch (err) {{
      startPolling();
    }}

    // Fallback polling
    function startPolling() {{
      if (pollInterval) return;
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

    // Render lucide icons if available
    if (window.lucide && typeof window.lucide.createIcons === "function") {{
      window.lucide.createIcons();
    }}
  </script>
</body>
</html>
"""


def render_services_catalog(services: List[WebServiceConfig], base_path: str = "/services") -> str:
    """Renders a dashboard catalog listing all configured web & API services using Obsidian Forge design."""
    cards_html = []
    for svc in services:
        name = html.escape(svc.name)
        title = html.escape(svc.title or svc.name.capitalize())
        desc = html.escape(svc.description or f"Service hosted at {svc.backend_url}")
        icon = html.escape(svc.icon or "⚡")
        path = svc.effective_path_prefix
        mode = html.escape(svc.mode)

        cards_html.append(f"""
        <div class="rounded-xl border border-zinc-800 bg-zinc-900 p-5 flex flex-col justify-between hover:border-zinc-700 transition-colors">
          <div>
            <div class="flex items-start justify-between gap-3 mb-3">
              <div class="w-10 h-10 rounded-lg bg-zinc-950 border border-zinc-800 flex items-center justify-center text-xl shrink-0">
                {icon}
              </div>
              <span class="text-[10px] font-mono tracking-wider text-zinc-400 bg-zinc-950 px-2 py-0.5 rounded border border-zinc-800 uppercase font-medium">
                {mode}
              </span>
            </div>
            <h3 class="text-base font-semibold text-white tracking-tight">{title}</h3>
            <p class="text-xs text-zinc-400 mt-1.5 line-clamp-2 leading-relaxed">{desc}</p>
          </div>
          <div class="mt-5 pt-3.5 border-t border-zinc-800/80 flex items-center justify-between gap-2">
            <span class="text-[11px] font-mono text-zinc-400 bg-zinc-950 px-2 py-1 rounded border border-zinc-800">
              <code>{path}</code>
            </span>
            <a href="{path}" class="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium bg-zinc-100 hover:bg-white text-zinc-900 transition-all active:scale-[0.98]">
              <span>Open</span>
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>
            </a>
          </div>
        </div>
        """)

    cards_joined = "\n".join(cards_html) if cards_html else "<p class='text-zinc-500 text-sm'>No services currently configured in config.yaml.</p>"
    count_str = f"{len(services)} SERVICES"

    return f"""<!DOCTYPE html>
<html class="dark" lang="en">
<head>
  <meta charset="utf-8">
  <meta content="width=device-width, initial-scale=1.0" name="viewport">
  <title>Llama Gateway &bull; Services Catalog</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            zinc: {{
              850: '#1f1f23',
              900: '#18181b',
              950: '#09090b',
            }}
          }},
          fontFamily: {{
            mono: ['JetBrains Mono', 'Fira Code', 'Menlo', 'Consolas', 'monospace'],
            sans: ['Geist', 'Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
          }}
        }}
      }}
    }};
  </script>
  <!-- Google Fonts: Inter, Geist & JetBrains Mono -->
  <link href="https://fonts.googleapis.com" rel="preconnect">
  <link crossorigin="" href="https://fonts.gstatic.com" rel="preconnect">
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&amp;family=Inter:wght@300;400;500;600;700&amp;family=JetBrains+Mono:wght@400;500;600&amp;display=swap" rel="stylesheet">
  <style data-purpose="background-effects">
    body {{
      background-color: #09090b;
      font-family: 'Geist', 'Inter', sans-serif;
      overflow-x: hidden;
    }}
    .grid-backdrop {{
      background-size: 32px 32px;
      background-image: 
        linear-gradient(to right, rgba(255, 255, 255, 0.02) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
    }}
  </style>
</head>
<body class="min-h-screen text-zinc-300 antialiased flex flex-col justify-between relative selection:bg-zinc-800 selection:text-white">
  <!-- Ambient Backdrop -->
  <div aria-hidden="true" class="fixed inset-0 pointer-events-none z-0 overflow-hidden">
    <div class="absolute inset-0 grid-backdrop opacity-100"></div>
  </div>

  <!-- TopBar -->
  <header class="relative z-10 w-full pt-8 pb-4 flex flex-col items-center justify-center px-4">
    <div class="inline-flex items-center gap-2.5 px-3 py-1 rounded-md border border-zinc-800 bg-zinc-900/90 text-xs font-mono tracking-tight text-zinc-400">
      <span class="flex h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
      <span class="font-medium text-zinc-200">LLAMA GATEWAY</span>
      <span class="text-zinc-600 font-mono">/</span>
      <span class="text-zinc-400">SERVICES CATALOG</span>
      <span class="text-zinc-600 font-mono">/</span>
      <span class="text-zinc-300 bg-zinc-800/80 px-1.5 py-0.5 rounded border border-zinc-700/60 text-[11px]">{count_str}</span>
    </div>
  </header>

  <!-- Main Content -->
  <main class="relative z-10 w-full max-w-5xl mx-auto px-4 py-6 flex-grow">
    <div class="mb-8 text-center sm:text-left">
      <h1 class="text-2xl font-semibold tracking-tight text-white">Services Catalog</h1>
      <p class="text-sm text-zinc-400 mt-1">Self-hosted web applications and API services managed with wake-on-request.</p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {cards_joined}
    </div>
  </main>

  <!-- Footer -->
  <footer class="relative z-10 w-full py-6 text-center text-xs text-zinc-500 font-mono space-y-1">
    <p>Powered by <span class="text-zinc-400 font-medium">Llama Gateway</span> &bull; Zero-idle energy saving architecture</p>
    <p class="text-[11px] text-zinc-600">Enterprise AI Infrastructure &bull; All nodes verified cold-boot compliant &bull; ISO 27001</p>
  </footer>
</body>
</html>
"""

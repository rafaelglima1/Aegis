"""FastAPI application — AEGIS V1.3."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from aegis.config import Settings, get_settings
from aegis.api.settings import router as settings_router
from aegis.api.websocket import router as ws_router, broadcast

import os
_log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=_log_level, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("aegis")


# AC-C10.1-11: API authentication boundary for sensitive endpoints.
# Production: AEGIS_API_KEY MUST be set. Missing key = fail-closed.
# Development: AEGIS_API_KEY not set = endpoints open (explicit opt-in).
API_KEY = os.getenv("AEGIS_API_KEY", "")
_ENVIRONMENT = os.getenv("TRADING_ENVIRONMENT", "SANDBOX")


async def require_api_key(authorization: str | None = Header(None)) -> None:
    """AC-C10.1-11/12: Require API key for sensitive endpoints.

    Accepts:
      Authorization: Bearer <api_key>

    Production behavior (TRADING_ENVIRONMENT=LIVE):
      - AEGIS_API_KEY must be set
      - Missing key = 401 Unauthorized (fail-closed)
      - Invalid key = 403 Forbidden

    Development behavior (TRADING_ENVIRONMENT=SANDBOX):
      - AEGIS_API_KEY not set = bypass (development mode)
      - AEGIS_API_KEY set = enforced
    """
    if not API_KEY:
        if _ENVIRONMENT == "LIVE":
            raise HTTPException(
                status_code=503,
                detail="AEGIS_API_KEY not configured. LIVE environment requires API key authentication.",
            )
        return  # Development mode — no auth required

    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = authorization.replace("Bearer ", "").strip()
    if token != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AEGIS V1.3 — Autonomous Swing Trader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e0e0e0; min-height: 100vh; }
        .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 16px 24px; border-bottom: 1px solid #2a2a4a; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 20px; color: #00d4ff; }
        .header .env { background: #00d4ff22; color: #00d4ff; padding: 4px 12px; border-radius: 12px; font-size: 12px; margin-left: 12px; }
        .header .env.live { background: #ff444422; color: #ff4444; }
        .nav { display: flex; gap: 4px; background: #12121f; padding: 4px; border-bottom: 1px solid #2a2a4a; }
        .nav button { background: transparent; color: #888; border: none; padding: 10px 20px; cursor: pointer; font-size: 13px; border-radius: 6px; }
        .nav button:hover { background: #1a1a2e; color: #e0e0e0; }
        .nav button.active { background: #00d4ff22; color: #00d4ff; }
        .container { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
        .card { background: #12121f; border: 1px solid #2a2a4a; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .card h2 { color: #00d4ff; font-size: 16px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
        .card h2::before { content: ''; width: 4px; height: 16px; background: #00d4ff; border-radius: 2px; }
        .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
        .metric { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 16px; text-align: center; }
        .metric .value { font-size: 24px; font-weight: 700; color: #00d4ff; }
        .metric .label { font-size: 11px; color: #888; text-transform: uppercase; margin-top: 4px; }
        .metric .value.green { color: #00ff88; }
        .metric .value.red { color: #ff4444; }
        .metric .value.yellow { color: #ffaa00; }
        .positions-table, .orders-table, .history-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .positions-table th, .orders-table th, .history-table th { text-align: left; padding: 10px 12px; background: #1a1a2e; color: #888; font-size: 11px; text-transform: uppercase; border-bottom: 1px solid #2a2a4a; }
        .positions-table td, .orders-table td, .history-table td { padding: 10px 12px; border-bottom: 1px solid #1a1a2e; }
        .positions-table tr:hover, .orders-table tr:hover { background: #1a1a2e; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
        .badge.long { background: #00ff8822; color: #00ff88; }
        .badge.short { background: #ff444422; color: #ff4444; }
        .badge.open { background: #00d4ff22; color: #00d4ff; }
        .badge.closed { background: #66666622; color: #888; }
        .badge.filled { background: #00ff8822; color: #00ff88; }
        .badge.pending { background: #ffaa0022; color: #ffaa00; }
        .badge.rejected { background: #ff444422; color: #ff4444; }
        .risk-bar { height: 8px; background: #1a1a2e; border-radius: 4px; overflow: hidden; margin-top: 8px; }
        .risk-bar .fill { height: 100%; transition: width 0.3s; }
        .risk-bar .fill.green { background: #00ff88; }
        .risk-bar .fill.yellow { background: #ffaa00; }
        .risk-bar .fill.red { background: #ff4444; }
        .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .field { margin-bottom: 12px; }
        .field label { display: block; color: #888; font-size: 11px; text-transform: uppercase; margin-bottom: 4px; }
        .field input, .field select { width: 100%; padding: 10px; background: #1a1a2e; border: 1px solid #3a3a5a; border-radius: 6px; color: #fff; font-size: 13px; }
        .field input:focus, .field select:focus { outline: none; border-color: #00d4ff; }
        .btn { background: #00d4ff; color: #000; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn:hover { background: #00b8d4; }
        .btn.danger { background: #ff4444; }
        .btn.danger:hover { background: #cc3333; }
        .btn:disabled { background: #3a3a5a; color: #666; cursor: not-allowed; }
        .status { padding: 6px 12px; border-radius: 6px; font-size: 12px; margin-top: 8px; display: none; }
        .status.ok { display: block; background: #00ff8822; color: #00ff88; }
        .status.error { display: block; background: #ff444422; color: #ff4444; }
        .toggle { position: relative; display: inline-block; width: 50px; height: 26px; }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .toggle .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #3a3a5a; border-radius: 26px; transition: .3s; }
        .toggle .slider:before { content: ''; position: absolute; height: 20px; width: 20px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: .3s; }
        .toggle input:checked + .slider { background: #00d4ff; }
        .toggle input:checked + .slider:before { transform: translateX(24px); }
        .danger { border-color: #ff444466; }
        .danger h2 { color: #ff4444; }
        .danger h2::before { background: #ff4444; }
        .info { border-color: #00ff8866; }
        .info h2 { color: #00ff88; }
        .info h2::before { background: #00ff88; }
        .setup-box { background: #1a1a2e; border: 1px solid #3a3a5a; border-radius: 8px; padding: 12px; margin-top: 12px; font-size: 12px; line-height: 1.6; }
        .setup-box h3 { color: #00d4ff; margin-bottom: 6px; font-size: 13px; }
        .setup-box code { background: #2a2a4a; padding: 2px 4px; border-radius: 3px; font-family: monospace; font-size: 11px; }
        .setup-box a { color: #00d4ff; }
        .empty { text-align: center; color: #666; padding: 40px; }
        .chart { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 16px; height: 200px; display: flex; align-items: center; justify-content: center; color: #666; }
        .ai-decision { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 12px; margin-bottom: 8px; }
        .ai-decision .header { display: flex; justify-content: space-between; margin-bottom: 8px; }
        .ai-decision .symbol { font-weight: 600; color: #00d4ff; }
        .ai-decision .action { font-weight: 600; }
        .ai-decision .action.long { color: #00ff88; }
        .ai-decision .action.close { color: #ff4444; }
        .ai-decision .thesis { font-size: 12px; color: #888; }
        .ai-decision .confidence { font-size: 11px; color: #666; margin-top: 4px; }
        .section { display: none; }
        .section.active { display: block; }
        @media (max-width: 768px) { .metrics { grid-template-columns: repeat(2, 1fr); } .row { grid-template-columns: 1fr; } }
        .modal-overlay { position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:1000 }
        .modal { background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:24px;width:420px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,0.5) }
        .modal h2 { margin-top:0;font-size:18px }
        .modal select, .modal input[type="number"], .modal input[type="date"] { width:100%;padding:8px;border:1px solid #333;border-radius:4px;background:#0d0d1a;color:#fff;font-size:13px;margin-top:4px;box-sizing:border-box }
        .modal label { font-size:12px;color:#888;display:block;margin-bottom:4px }
        .btn.secondary { background:#333;color:#aaa }
        .btn.secondary:hover { background:#444;color:#fff }
    </style>
</head>
<body>
    <div class="header">
        <h1>AEGIS <span class="env" id="env-badge">SANDBOX</span></h1>
        <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:#888" id="clock"></span>
            <button class="btn" onclick="manualRun()" style="padding:6px 12px;font-size:12px">Executar Agora</button>
            <button class="btn" onclick="location.href='/'" style="padding:6px 12px;font-size:12px">Config</button>
        </div>
    </div>
    <div class="nav">
        <button class="active" onclick="showSection('dashboard')">Dashboard</button>
        <button onclick="showSection('positions')">Posições</button>
        <button onclick="showSection('orders')">Ordens</button>
        <button onclick="showSection('history')">Histórico</button>
                <button onclick="showSection('ai')">Decisões IA</button>
        <button onclick="showSection('risk')">Risco</button>
        <button onclick="showSection('config')">Config</button>
    </div>
    <div class="container">
        <!-- DASHBOARD -->
        <div class="section active" id="sec-dashboard">
            <div class="metrics">
                <div class="metric">
                    <div class="value" id="m-capital">R$ 100.00</div>
                    <div class="label">Capital Virtual</div>
                </div>
                <div class="metric">
                    <div class="value green" id="m-pnl">R$ 0.00</div>
                    <div class="label">P&L Hoje</div>
                </div>
                <div class="metric">
                    <div class="value" id="m-positions">0</div>
                    <div class="label">Posições Abertas</div>
                </div>
                <div class="metric">
                    <div class="value" id="m-exposure">0%</div>
                    <div class="label">Exposição</div>
                </div>
            </div>
            <div class="row">
                <div class="card">
                    <h2>Posições Abertas</h2>
                    <div id="dash-positions"><div class="empty">Nenhuma posição aberta</div></div>
                </div>
                <div class="card">
                    <h2>Últimas Decisões IA</h2>
                    <div id="dash-ai"><div class="empty">Nenhuma decisão ainda</div></div>
                </div>
            </div>
            <div class="card">
                <h2>Equity Curve</h2>
                <div class="chart">Gráfico de equity será exibido aqui</div>
            </div>
        </div>

        <!-- POSIÇÕES -->
        <div class="section" id="sec-positions">
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <h2>Posições Abertas</h2>
                    <button class="btn" onclick="openExport('positions')">Exportar JSON</button>
                </div>
                <table class="positions-table">
                    <thead><tr><th>Par</th><th>Lado</th><th>Qtd</th><th>Preço Entrada</th><th>Preço Atual</th><th>P&L</th><th>Stop</th><th>Take Profit</th><th>Ações</th></tr></thead>
                    <tbody id="positions-body"><tr><td colspan="9" class="empty">Nenhuma posição aberta</td></tr></tbody>
                </table>
            </div>
        </div>

        <!-- ORDERS -->
        <div class="section" id="sec-orders">
            <div class="card">
                <h2>Nova Ordem</h2>
                <div class="row">
                    <div class="field">
                        <label>Par</label>
                        <select id="order-symbol"><option>BTC-BRL</option><option>ETH-BRL</option><option>SOL-BRL</option></select>
                    </div>
                    <div class="field">
                        <label>Ação</label>
                        <select id="order-action"><option value="LONG">LONG (Comprar)</option><option value="CLOSE">CLOSE (Vender)</option></select>
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Preço Entrada</label>
                        <input type="number" id="order-price" step="0.01" placeholder="0.00">
                    </div>
                    <div class="field">
                        <label>Quantidade</label>
                        <input type="number" id="order-qty" step="0.0001" placeholder="0.0000">
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Stop Loss</label>
                        <input type="number" id="order-stop" step="0.01" placeholder="0.00">
                    </div>
                    <div class="field">
                        <label>Take Profit</label>
                        <input type="number" id="order-tp" step="0.01" placeholder="0.00">
                    </div>
                </div>
                <button class="btn" onclick="placeOrder()">Enviar Ordem</button>
                <div class="status" id="order-status"></div>
            </div>
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <h2>Ordens Ativas</h2>
                    <button class="btn" onclick="openExport('orders')">Exportar JSON</button>
                </div>
                <table class="orders-table">
                    <thead><tr><th>ID</th><th>Par</th><th>Lado</th><th>Qtd</th><th>Preço</th><th>Status</th><th>Timestamp</th></tr></thead>
                    <tbody id="orders-body"><tr><td colspan="7" class="empty">Nenhuma ordem ativa</td></tr></tbody>
                </table>
            </div>
        </div>

        <!-- HISTÓRICO -->
        <div class="section" id="sec-history">
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <h2>Histórico de Trades</h2>
                    <button class="btn" onclick="openExport('history')">Exportar JSON</button>
                </div>
                <table class="history-table">
                    <thead><tr><th>Data</th><th>Par</th><th>Lado</th><th>Qtd</th><th>Entrada</th><th>Saída</th><th>P&L</th><th>Fee</th></tr></thead>
                    <tbody id="history-body"><tr><td colspan="8" class="empty">Nenhum trade executado</td></tr></tbody>
                </table>
            </div>
        </div>

        <!-- AI DECISIONS -->
        <div class="section" id="sec-ai">
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <h2>Decisões da IA</h2>
                    <button class="btn" onclick="openExport('decisions')">Exportar JSON</button>
                </div>
                <div id="ai-decisions"><div class="empty">Nenhuma decisão registrada</div></div>
            </div>
        </div>

        <!-- RISCO -->
        <div class="section" id="sec-risk">
            <div class="card">
                <h2>Limites de Risco</h2>
                <div class="metrics">
                    <div class="metric">
                        <div class="value" id="risk-capital">R$ 100.00</div>
                        <div class="label">Capital Ref.</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-max-trade">1%</div>
                        <div class="label">Risco/Trade</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-max-pos">1</div>
                        <div class="label">Max Posições</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-min-conf">50%</div>
                        <div class="label">Confiança Min</div>
                    </div>
                </div>
                <div class="metrics" style="margin-top:8px">
                    <div class="metric">
                        <div class="value" id="risk-max-daily">5%</div>
                        <div class="label">Perda Diária Max</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-max-pos-size">20%</div>
                        <div class="label">Tam. Max Posição</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-max-exposure">100%</div>
                        <div class="label">Exposição Max</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-circuit">10%</div>
                        <div class="label">Circuit Breaker</div>
                    </div>
                </div>
            </div>
            <div class="card">
                <h2>Regras Ativas</h2>
                <div class="metrics" style="grid-template-columns:1fr 1fr">
                    <div class="metric">
                        <div class="value" id="risk-stop-status" style="font-size:14px">--</div>
                        <div class="label">Stop Loss</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-tp-status" style="font-size:14px">--</div>
                        <div class="label">Take Profit</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-longonly-status" style="font-size:14px">--</div>
                        <div class="label">Modo Direção</div>
                    </div>
                    <div class="metric">
                        <div class="value" id="risk-kill-status" style="font-size:14px">--</div>
                        <div class="label">Kill Switch</div>
                    </div>
                </div>
            </div>
            <div class="card">
                <h2>Exposição Atual</h2>
                <div style="margin-bottom:12px">
                    <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
                        <span>Exposição: <span id="risk-exposure">0%</span></span>
                        <span>Limite: <span id="risk-exposure-limit">100%</span></span>
                    </div>
                    <div class="risk-bar"><div class="fill green" id="risk-bar" style="width:0%"></div></div>
                </div>
                <h2>Drawdown Hoje</h2>
                <div>
                    <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
                        <span>Drawdown: <span id="risk-drawdown">0%</span></span>
                        <span>Limite: <span id="risk-drawdown-limit">10%</span></span>
                    </div>
                    <div class="risk-bar"><div class="fill green" id="drawdown-bar" style="width:0%"></div></div>
                </div>
            </div>
            <div class="card danger">
                <h2>Kill Switch</h2>
                <div class="field">
                    <label>Ativar Kill Switch (bloqueia todas as ordens)</label>
                    <label class="toggle">
                        <input type="checkbox" id="kill-switch" onchange="toggleKillSwitch()">
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="status" id="kill-status"></div>
            </div>
        </div>

        <!-- CONFIG -->
        <div class="section" id="sec-config">
            <div class="card info">
                <h2>Mercado Bitcoin — API</h2>
                <div class="setup-box">
                    <h3>Como obter suas API keys:</h3>
                    1. Acesse <a href="https://www.mercadobitcoin.com.br/api-keys" target="_blank">mercadobitcoin.com.br/api-keys</a><br>
                    2. Clique em "Criar nova API key"<br>
                    3. Dê um nome (ex: "AEGIS Bot")<br>
                    4. Permissões: marque <code>Leitura</code> e <code>Trade</code><br>
                    5. Copie a <code>API Key</code> e o <code>Secret</code><br>
                    6. Cole abaixo
                </div>
            </div>
            <div class="card">
                <h2>Broker — Mercado Bitcoin</h2>
                <div class="row">
                    <div class="field">
                        <label>API Key</label>
                        <input type="password" id="broker-api-key" placeholder="Cole sua API Key aqui">
                    </div>
                    <div class="field">
                        <label>API Secret</label>
                        <input type="password" id="broker-api-secret" placeholder="Cole seu Secret aqui">
                    </div>
                </div>
                <button class="btn" onclick="saveBroker()">Salvar Broker</button>
                <div class="status" id="broker-status"></div>
            </div>
            <div class="card">
                <h2>LLM Provider</h2>
                <div class="field">
                    <label>API Base URL</label>
                    <input type="text" id="llm-base-url" placeholder="https://api.kilo.ai/api/gateway">
                    <div class="hint">URL da API do provider (OpenAI-compatible). Ex: Kilo AI, OpenAI, Groq, Together</div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Modelo</label>
                        <input type="text" id="llm-model" placeholder="kilo-auto/free">
                    </div>
                    <div class="field">
                        <label>API Key</label>
                        <input type="password" id="llm-api-key" placeholder="sk-...">
                    </div>
                </div>
                <button class="btn" onclick="saveLLM()">Salvar LLM</button>
                <div class="status" id="llm-status"></div>
            </div>
            <div class="card">
                <h2>Trading</h2>
                <div class="field">
                    <label>Pares de trading (separados por vírgula)</label>
                    <input type="text" id="trading-symbols" placeholder="BTC-BRL,ETH-BRL,SOL-BRL">
                </div>
                <div class="row">
                    <div class="field">
                        <label>Timeframe</label>
                        <select id="trading-timeframe">
                            <option value="1h" selected>1 hora</option>
                            <option value="4h">4 horas</option>
                            <option value="1d">1 dia</option>
                        </select>
                    </div>
                    <div class="field">
                        <label>Ambiente</label>
                        <select id="trading-env">
                            <option value="SANDBOX" selected>SANDBOX (simulação)</option>
                            <option value="LIVE">LIVE (dinheiro real)</option>
                        </select>
                    </div>
                </div>
                <button class="btn" onclick="saveTrading()">Salvar Trading</button>
                <div class="status" id="trading-status"></div>
            </div>
            <div class="card">
                <h2>Capital & Risco</h2>
                <div class="row">
                    <div class="field">
                        <label>Capital Virtual (R$)</label>
                        <input type="number" id="trading-capital" step="10" placeholder="100">
                    </div>
                    <div class="field">
                        <label>Risco por Trade (%)</label>
                        <input type="number" id="trading-risk-pct" step="0.1" min="0.1" max="10" placeholder="1.0">
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Max Posições Simultâneas</label>
                        <input type="number" id="trading-max-positions" min="1" max="10" step="1" placeholder="1">
                    </div>
                    <div class="field">
                        <label>Min. Confiança IA (0-1)</label>
                        <input type="number" id="trading-min-confidence" step="0.05" min="0" max="1" placeholder="0.5">
                    </div>
                </div>
                <button class="btn" onclick="saveTrading()">Salvar Capital & Risco</button>
                <div class="status" id="trading-status-2"></div>
            </div>
            <div class="card">
                <h2>Regras de Negócio</h2>
                <div class="row">
                    <div class="field">
                        <label>Stop Loss Obrigatório</label>
                        <label class="toggle">
                            <input type="checkbox" id="trading-mandatory-stop" checked>
                            <span class="slider"></span>
                        </label>
                    </div>
                    <div class="field">
                        <label>Take Profit Obrigatório</label>
                        <label class="toggle">
                            <input type="checkbox" id="trading-mandatory-tp" checked>
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Apenas LONG (sem SHORT)</label>
                        <label class="toggle">
                            <input type="checkbox" id="trading-long-only" checked>
                            <span class="slider"></span>
                        </label>
                    </div>
                    <div class="field">
                        <label>Alavancagem (0 = spot)</label>
                        <input type="number" id="trading-leverage" min="0" max="10" step="1" placeholder="0">
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Perda Diária Max (% do capital)</label>
                        <input type="number" id="trading-max-daily-loss" step="0.5" min="0.5" max="50" placeholder="5.0">
                    </div>
                    <div class="field">
                        <label>Tam. Max Posição (% do capital)</label>
                        <input type="number" id="trading-max-pos-size" step="1" min="1" max="100" placeholder="20.0">
                    </div>
                </div>
                <div class="row">
                    <div class="field">
                        <label>Exposição Max (% do capital)</label>
                        <input type="number" id="trading-max-exposure" step="5" min="10" max="100" placeholder="100.0">
                    </div>
                    <div class="field">
                        <label>Circuit Breaker (% drawdown)</label>
                        <input type="number" id="trading-circuit-breaker" step="1" min="1" max="50" placeholder="10">
                    </div>
                </div>
                <button class="btn" onclick="saveTrading()">Salvar Regras</button>
                <div class="status" id="trading-status-3"></div>
            </div>
            <div class="card danger">
                <h2>Modo LIVE</h2>
                <div class="field">
                    <label>Habilitar trading com dinheiro real</label>
                    <label class="toggle">
                        <input type="checkbox" id="live-enabled">
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="status" id="live-status"></div>
            </div>
            <div class="card">
                <h2>Prompt do LLM</h2>
                <p style="font-size:12px;color:#666;margin-top:0;margin-bottom:8px">
                    Template do prompt enviado ao modelo. Use <code>{market_state}</code> e <code>{portfolio}</code> como placeholders.
                    Variáveis dinâmicas são substituídas pelo worker.
                </p>
                <div class="field">
                    <textarea id="trading-prompt" rows="18" style="width:100%;padding:10px;border:1px solid #333;border-radius:4px;background:#0d0d1a;color:#fff;font-family:monospace;font-size:12px;resize:vertical;box-sizing:border-box"></textarea>
                </div>
                <div style="display:flex;gap:8px;align-items:center">
                    <button class="btn" onclick="saveTrading()">Salvar Prompt</button>
                    <button class="btn secondary" onclick="resetPrompt()">Restaurar Padrão</button>
                </div>
                <div class="status" id="trading-status-4"></div>
            </div>
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <h2>Exportar Configuração</h2>
                    <button class="btn" onclick="exportConfig()">Exportar JSON (sem keys)</button>
                </div>
                <p style="font-size:12px;color:#666">Exporta todas as configurações atuais. API keys e secrets são excluídos por segurança.</p>
            </div>
        </div>
    </div>

    <!-- Export Modal -->
    <div class="modal-overlay" id="modal-export" style="display:none" onclick="closeModal('modal-export')">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 id="export-modal-title">Exportar Dados</h2>
            <p style="color:#aaa;font-size:13px;margin-bottom:16px">Escolha como deseja filtrar os registros antes de exportar.</p>
            <div class="field">
                <label>Modo de seleção</label>
                <select id="export-mode" onchange="toggleExportMode()">
                    <option value="all">Todos os registros</option>
                    <option value="qty">Últimos N registros</option>
                    <option value="range">Range de datas</option>
                </select>
            </div>
            <div id="export-qty-field" class="field" style="display:none">
                <label>Quantidade de registros</label>
                <input type="number" id="export-qty" min="1" step="1" placeholder="Ex: 100">
            </div>
            <div id="export-range-field" style="display:none">
                <div class="row">
                    <div class="field">
                        <label>Data inicial</label>
                        <input type="date" id="export-date-from">
                    </div>
                    <div class="field">
                        <label>Data final</label>
                        <input type="date" id="export-date-to">
                    </div>
                </div>
            </div>
            <div style="display:flex;gap:8px;margin-top:16px">
                <button class="btn" onclick="doExport()">Exportar</button>
                <button class="btn secondary" onclick="closeModal('modal-export')">Cancelar</button>
            </div>
        </div>
    </div>

    <script>
        let state = { positions: [], orders: [], history: [], decisions: [], pnl: 0 };

        function showSection(name) {
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            document.getElementById('sec-' + name).classList.add('active');
            event.target.classList.add('active');
        }

        function updateClock() {
            document.getElementById('clock').textContent = new Date().toLocaleString('pt-BR');
        }
        setInterval(updateClock, 1000);
        updateClock();

        async function loadAll() {
            try {
                const res = await fetch('/api/state');
                if (res.ok) state = await res.json();
            } catch(e) {}
            renderDashboard();
            renderPositions();
            renderOrders();
            renderHistory();
            renderAI();
            renderRisk();
        }

        function renderDashboard() {
            const capital = parseFloat(state.capital) || 100;
            const pnl = parseFloat(state.pnl) || 0;
            document.getElementById('m-capital').textContent = 'R$ ' + capital.toFixed(2);
            document.getElementById('m-pnl').textContent = 'R$ ' + pnl.toFixed(2);
            document.getElementById('m-pnl').className = 'value ' + (pnl >= 0 ? 'green' : 'red');
            document.getElementById('m-positions').textContent = (state.positions || []).length;
            const exposure = capital > 0 ? ((parseFloat(state.exposure) || 0) / capital * 100) : 0;
            document.getElementById('m-exposure').textContent = exposure.toFixed(1) + '%';

            const posHtml = (state.positions || []).map(p => {
                const pnlVal = parseFloat(p.pnl) || 0;
                const pnlPct = parseFloat(p.pnl_pct) || 0;
                const pnlColor = pnlVal >= 0 ? '#00ff88' : '#ff4444';
                return `
                <div class="ai-decision">
                    <div class="header">
                        <span class="symbol">${p.symbol}</span>
                        <span class="action long">${p.side}</span>
                    </div>
                    <div class="thesis">Entrada: R$ ${parseFloat(p.entry_price).toFixed(2)} | Atual: R$ ${parseFloat(p.current_price).toFixed(2)}</div>
                    <div class="confidence" style="color:${pnlColor}">P&L: R$ ${pnlVal.toFixed(2)} (${pnlPct.toFixed(2)}%)</div>
                    ${p.stop_loss ? `<div style="font-size:11px;color:#666">SL: R$ ${parseFloat(p.stop_loss).toFixed(2)} | TP: R$ ${p.take_profit ? parseFloat(p.take_profit).toFixed(2) : '--'}</div>` : ''}
                </div>`;
            }).join('') || '<div class="empty">Nenhuma posição aberta</div>';
            document.getElementById('dash-positions').innerHTML = posHtml;

            const aiHtml = (state.decisions || []).slice(-3).reverse().map(d => `
                <div class="ai-decision">
                    <div class="header">
                        <span class="symbol">${d.symbol || '--'}</span>
                        <span class="action ${d.action?.toLowerCase()}">${d.action}</span>
                    </div>
                    <div class="thesis">${d.thesis || ''}</div>
                    <div class="confidence">Confiança: ${(d.confidence * 100).toFixed(0)}%</div>
                </div>
            `).join('') || '<div class="empty">Nenhuma decisão ainda</div>';
            document.getElementById('dash-ai').innerHTML = aiHtml;
        }

        function renderPositions() {
            const html = (state.positions || []).map(p => {
                const pnlVal = parseFloat(p.pnl) || 0;
                const pnlColor = pnlVal >= 0 ? '#00ff88' : '#ff4444';
                return `
                <tr>
                    <td><strong>${p.symbol}</strong></td>
                    <td><span class="badge long">${p.side}</span></td>
                    <td>${parseFloat(p.quantity).toFixed(8)}</td>
                    <td>R$ ${parseFloat(p.entry_price).toFixed(2)}</td>
                    <td>R$ ${parseFloat(p.current_price).toFixed(2)}</td>
                    <td style="color:${pnlColor}">R$ ${pnlVal.toFixed(2)}</td>
                    <td>R$ ${p.stop_loss ? parseFloat(p.stop_loss).toFixed(2) : '--'}</td>
                    <td>R$ ${p.take_profit ? parseFloat(p.take_profit).toFixed(2) : '--'}</td>
                    <td><button class="btn danger" style="padding:4px 8px;font-size:11px" onclick="closePosition('${p.id}')">Fechar</button></td>
                </tr>`;
            }).join('');
            document.getElementById('positions-body').innerHTML = html || '<tr><td colspan="9" class="empty">Nenhuma posição aberta</td></tr>';
        }

        function renderOrders() {
            const html = (state.orders || []).map(o => `
                <tr>
                    <td style="font-family:monospace;font-size:11px">${o.id?.slice(0,8)}</td>
                    <td>${o.symbol}</td>
                    <td><span class="badge ${o.side?.toLowerCase()}">${o.side}</span></td>
                    <td>${parseFloat(o.quantity).toFixed(8)}</td>
                    <td>R$ ${parseFloat(o.price).toFixed(2)}</td>
                    <td><span class="badge ${o.status?.toLowerCase()}">${o.status}</span></td>
                    <td>${o.timestamp || '--'}</td>
                </tr>
            `).join('');
            document.getElementById('orders-body').innerHTML = html || '<tr><td colspan="7" class="empty">Nenhuma ordem ativa</td></tr>';
        }

        function renderHistory() {
            const html = (state.history || []).map(t => {
                const pnlVal = parseFloat(t.pnl) || 0;
                const pnlColor = pnlVal >= 0 ? '#00ff88' : '#ff4444';
                return `
                <tr>
                    <td>${t.date || '--'}</td>
                    <td>${t.symbol}</td>
                    <td><span class="badge ${t.side?.toLowerCase()}">${t.side}</span></td>
                    <td>${parseFloat(t.quantity).toFixed(8)}</td>
                    <td>R$ ${parseFloat(t.entry_price).toFixed(2)}</td>
                    <td>R$ ${parseFloat(t.exit_price).toFixed(2)}</td>
                    <td style="color:${pnlColor}">R$ ${pnlVal.toFixed(2)}</td>
                    <td>R$ ${parseFloat(t.fee || 0).toFixed(2)}</td>
                </tr>`;
            }).join('');
            document.getElementById('history-body').innerHTML = html || '<tr><td colspan="8" class="empty">Nenhum trade executado</td></tr>';
        }

        function renderAI() {
            const html = (state.decisions || []).map(d => `
                <div class="ai-decision">
                    <div class="header">
                        <span class="symbol">${d.symbol || '--'} — ${d.model || 'LLM'}</span>
                        <span class="action ${d.action?.toLowerCase()}">${d.action}</span>
                    </div>
                    <div class="thesis">${d.thesis || ''}</div>
                    <div class="confidence">Confiança: ${(d.confidence * 100).toFixed(0)}% | Provider: ${d.provider || '--'}</div>
                    <div style="font-size:11px;color:#666;margin-top:4px">${d.reasoning || ''}</div>
                </div>
            `).join('');
            document.getElementById('ai-decisions').innerHTML = html || '<div class="empty">Nenhuma decisão registrada</div>';
        }

        function renderRisk() {
            const rl = state.risk_limits || {};
            // Limits
            const capital = parseFloat(rl.reference_capital || state.capital) || 100;
            document.getElementById('risk-capital').textContent = 'R$ ' + capital.toFixed(2);
            document.getElementById('risk-max-trade').textContent = (rl.max_risk_per_trade_pct || '1') + '%';
            document.getElementById('risk-max-pos').textContent = rl.max_positions || 1;
            document.getElementById('risk-min-conf').textContent = ((parseFloat(rl.min_confidence) || 0.5) * 100).toFixed(0) + '%';
            document.getElementById('risk-max-daily').textContent = (rl.max_daily_loss_pct || '5') + '%';
            document.getElementById('risk-max-pos-size').textContent = (rl.max_position_size_pct || '20') + '%';
            document.getElementById('risk-max-exposure').textContent = (rl.max_exposure_pct || '100') + '%';
            document.getElementById('risk-circuit').textContent = (rl.circuit_breaker_pct || '10') + '%';
            // Rules
            document.getElementById('risk-stop-status').textContent = rl.mandatory_stop ? 'OBRIGATÓRIO' : 'Opcional';
            document.getElementById('risk-stop-status').style.color = rl.mandatory_stop ? '#00ff88' : '#ffaa00';
            document.getElementById('risk-tp-status').textContent = rl.mandatory_take_profit ? 'OBRIGATÓRIO' : 'Opcional';
            document.getElementById('risk-tp-status').style.color = rl.mandatory_take_profit ? '#00ff88' : '#ffaa00';
            document.getElementById('risk-longonly-status').textContent = rl.long_only ? 'LONG ONLY' : 'LONG + SHORT';
            document.getElementById('risk-longonly-status').style.color = rl.long_only ? '#00ff88' : '#ffaa00';
            document.getElementById('risk-kill-status').textContent = state.kill_switch ? 'ATIVADO' : 'Desligado';
            document.getElementById('risk-kill-status').style.color = state.kill_switch ? '#ff4444' : '#00ff88';
            // Exposure bar
            const exposure = parseFloat(state.exposure) || 0;
            const exposurePct = capital > 0 ? (exposure / capital * 100) : 0;
            const maxExposurePct = parseFloat(rl.max_exposure_pct) || 100;
            document.getElementById('risk-exposure').textContent = exposurePct.toFixed(1) + '%';
            document.getElementById('risk-exposure-limit').textContent = maxExposurePct + '%';
            document.getElementById('risk-bar').style.width = Math.min(exposurePct, 100) + '%';
            document.getElementById('risk-bar').className = 'fill ' + (exposurePct < 50 ? 'green' : exposurePct < 80 ? 'yellow' : 'red');
            // Drawdown bar — C7-05: uses equity (cash + unrealized_pnl), not just cash
            const equity = parseFloat(state.equity) || capital;
            const peakEquity = parseFloat(state.peak_equity) || equity;
            const drawdown = peakEquity > 0 ? ((peakEquity - equity) / peakEquity * 100) : 0;
            const circuitBreaker = parseFloat(rl.circuit_breaker_pct) || 10;
            document.getElementById('risk-drawdown').textContent = Math.max(0, drawdown).toFixed(1) + '%';
            document.getElementById('risk-drawdown-limit').textContent = circuitBreaker + '%';
            document.getElementById('drawdown-bar').style.width = Math.max(0, drawdown) + '%';
            document.getElementById('drawdown-bar').className = 'fill ' + (drawdown < 5 ? 'green' : drawdown < circuitBreaker ? 'yellow' : 'red');
        }

        async function placeOrder() {
            const status = document.getElementById('order-status');
            const data = {
                symbol: document.getElementById('order-symbol').value,
                action: document.getElementById('order-action').value,
                entry_price: parseFloat(document.getElementById('order-price').value),
                quantity: parseFloat(document.getElementById('order-qty').value),
                stop_loss: parseFloat(document.getElementById('order-stop').value) || null,
                take_profit: parseFloat(document.getElementById('order-tp').value) || null,
            };
            if (!data.entry_price || !data.quantity) {
                status.className = 'status error';
                status.textContent = 'Preço e quantidade são obrigatórios';
                return;
            }
            try {
                const res = await fetch('/api/trade', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
                const result = await res.json();
                status.className = result.status === 'ok' ? 'status ok' : 'status error';
                status.textContent = result.message || result.error;
                if (result.status === 'ok') loadAll();
            } catch(e) {
                status.className = 'status error';
                status.textContent = 'Erro ao enviar ordem';
            }
        }

        async function closePosition(id) {
            if (!confirm('Fechar esta posição?')) return;
            await fetch('/api/position/' + id + '/close', { method: 'POST' });
            loadAll();
        }

        async function toggleKillSwitch() {
            const active = document.getElementById('kill-switch').checked;
            const status = document.getElementById('kill-status');
            await fetch('/api/risk/kill-switch', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ active }) });
            status.className = active ? 'status error' : 'status ok';
            status.textContent = active ? 'Kill switch ATIVADO — nenhuma ordem será executada' : 'Kill switch desativado';
        }

        async function loadSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                document.getElementById('llm-base-url').value = data.llm.base_url;
                document.getElementById('llm-model').value = data.llm.model;
                document.getElementById('llm-api-key').placeholder = data.llm.api_key;
                document.getElementById('broker-api-key').placeholder = data.broker.api_key;
                document.getElementById('broker-api-secret').placeholder = data.broker.api_secret;
                // Trading
                document.getElementById('trading-symbols').value = data.trading.symbols;
                document.getElementById('trading-timeframe').value = data.trading.timeframe;
                document.getElementById('trading-env').value = data.trading.trading_environment;
                document.getElementById('live-enabled').checked = data.trading.live_enabled;
                // Capital & Risk
                document.getElementById('trading-capital').value = data.trading.capital;
                document.getElementById('trading-risk-pct').value = data.trading.risk_per_trade_pct;
                document.getElementById('trading-max-positions').value = data.trading.max_positions;
                document.getElementById('trading-min-confidence').value = data.trading.min_confidence;
                // Business Rules
                document.getElementById('trading-mandatory-stop').checked = data.trading.mandatory_stop;
                document.getElementById('trading-mandatory-tp').checked = data.trading.mandatory_take_profit;
                document.getElementById('trading-long-only').checked = data.trading.long_only;
                document.getElementById('trading-leverage').value = data.trading.leverage;
                document.getElementById('trading-max-daily-loss').value = data.trading.max_daily_loss_pct;
                document.getElementById('trading-max-pos-size').value = data.trading.max_position_size_pct;
                document.getElementById('trading-max-exposure').value = data.trading.max_exposure_pct;
                document.getElementById('trading-circuit-breaker').value = data.trading.circuit_breaker_pct;
                document.getElementById('trading-prompt').value = data.trading.prompt_template;
                // Badge
                document.getElementById('env-badge').textContent = data.trading.trading_environment;
                document.getElementById('env-badge').className = 'env' + (data.trading.trading_environment === 'LIVE' ? ' live' : '');
            } catch(e) {}
        }

        async function saveLLM() {
            const status = document.getElementById('llm-status');
            const api_key = document.getElementById('llm-api-key').value;
            await fetch('/api/settings/llm', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ base_url: document.getElementById('llm-base-url').value, model: document.getElementById('llm-model').value, api_key: api_key || '***' }) });
            status.className = 'status ok';
            status.textContent = 'LLM salvo com sucesso';
            document.getElementById('llm-api-key').value = '';
        }

        async function saveBroker() {
            const status = document.getElementById('broker-status');
            const key = document.getElementById('broker-api-key').value;
            const secret = document.getElementById('broker-api-secret').value;
            await fetch('/api/settings/broker', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ api_key: key || '***', api_secret: secret || '***' }) });
            status.className = 'status ok';
            status.textContent = 'Broker salvo com sucesso';
            document.getElementById('broker-api-key').value = '';
            document.getElementById('broker-api-secret').value = '';
        }

        async function saveTrading() {
            const payload = {
                trading_environment: document.getElementById('trading-env').value,
                live_enabled: document.getElementById('live-enabled').checked,
                symbols: document.getElementById('trading-symbols').value,
                timeframe: document.getElementById('trading-timeframe').value,
                capital: parseFloat(document.getElementById('trading-capital').value) || 100.0,
                risk_per_trade_pct: parseFloat(document.getElementById('trading-risk-pct').value) || 1.0,
                max_positions: parseInt(document.getElementById('trading-max-positions').value) || 1,
                min_confidence: parseFloat(document.getElementById('trading-min-confidence').value) || 0.5,
                mandatory_stop: document.getElementById('trading-mandatory-stop').checked,
                mandatory_take_profit: document.getElementById('trading-mandatory-tp').checked,
                long_only: document.getElementById('trading-long-only').checked,
                leverage: parseInt(document.getElementById('trading-leverage').value) || 0,
                max_daily_loss_pct: parseFloat(document.getElementById('trading-max-daily-loss').value) || 5.0,
                max_position_size_pct: parseFloat(document.getElementById('trading-max-pos-size').value) || 20.0,
                max_exposure_pct: parseFloat(document.getElementById('trading-max-exposure').value) || 100.0,
                circuit_breaker_pct: parseFloat(document.getElementById('trading-circuit-breaker').value) || 10.0,
                prompt_template: document.getElementById('trading-prompt').value || '',
            };
            await fetch('/api/settings/trading', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            ['trading-status', 'trading-status-2', 'trading-status-3', 'trading-status-4'].forEach(id => {
                const el = document.getElementById(id);
                if (el) { el.className = 'status ok'; el.textContent = 'Salvo com sucesso'; }
            });
        }

        document.getElementById('live-enabled').addEventListener('change', function() {
            const status = document.getElementById('live-status');
            if (this.checked) {
                if (!confirm('ATENÇÃO: Isso habilita trading com DINHEIRO REAL na Mercado Bitcoin.\\n\\nTem certeza?')) { this.checked = false; return; }
                status.className = 'status error';
                status.textContent = 'LIVE habilitado — reinicie os containers para ativar';
                document.getElementById('env-badge').textContent = 'LIVE';
                document.getElementById('trading-env').value = 'LIVE';
            } else {
                status.className = 'status ok';
                status.textContent = 'SANDBOX habilitado';
                document.getElementById('env-badge').textContent = 'SANDBOX';
                document.getElementById('trading-env').value = 'SANDBOX';
            }
        });

        loadSettings();
        loadAll();
        setInterval(loadAll, 10000);

        async function manualRun() {
            const btn = event.target;
            btn.disabled = true; btn.textContent = 'Executando...';
            try {
                const res = await fetch('/api/run', { method: 'POST' });
                const data = await res.json();
                btn.textContent = data.status === 'ok' ? 'Concluído!' : 'Erro';
                setTimeout(() => { btn.disabled = false; btn.textContent = 'Executar Agora'; }, 2000);
                loadAll();
            } catch(e) {
                btn.textContent = 'Erro';
                setTimeout(() => { btn.disabled = false; btn.textContent = 'Executar Agora'; }, 2000);
            }
        }

        // WebSocket for real-time updates
        let ws;
        function connectWS() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(protocol + '//' + location.host + '/ws');
            ws.onmessage = function(e) {
                try {
                    const data = JSON.parse(e.data);
                    if (data.type === 'state_update') {
                        // Merge worker state into local state
                        Object.assign(state, data);
                        delete state.type;
                        renderDashboard();
                        renderPositions();
                        renderOrders();
                        renderHistory();
                        renderAI();
                        renderRisk();
                    } else if (data.type === 'new_order') {
                        state.orders.push(data.order);
                        renderOrders();
                    } else if (data.type === 'position_closed') {
                        loadAll();
                    } else if (data.type === 'kill_switch') {
                        document.getElementById('kill-switch').checked = data.active;
                    }
                } catch(err) {}
            };
            ws.onclose = function() { setTimeout(connectWS, 5000); };
            ws.onerror = function() { ws.close(); };
        }
        connectWS();

        // === EXPORT FUNCTIONS ===
        let _exportSection = null;

        function downloadJSON(data, filename) {
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click();
            document.body.removeChild(a); URL.revokeObjectURL(url);
        }

        const _exportTitles = {
            positions: 'Exportar Posições',
            orders: 'Exportar Ordens',
            history: 'Exportar Histórico',
            decisions: 'Exportar Decisões IA',
        };

        function openExport(section) {
            _exportSection = section;
            document.getElementById('export-modal-title').textContent = _exportTitles[section] || 'Exportar Dados';
            document.getElementById('modal-export').style.display = 'flex';
            toggleExportMode();
        }

        function closeModal(id) {
            document.getElementById(id).style.display = 'none';
        }

        function toggleExportMode() {
            const mode = document.getElementById('export-mode').value;
            document.getElementById('export-qty-field').style.display = mode === 'qty' ? 'block' : 'none';
            document.getElementById('export-range-field').style.display = mode === 'range' ? 'block' : 'none';
        }

        async function doExport() {
            const mode = document.getElementById('export-mode').value;
            let records = state[_exportSection] || [];

            if (mode === 'qty') {
                const qty = parseInt(document.getElementById('export-qty').value) || records.length;
                records = records.slice(-qty);
            } else if (mode === 'range') {
                const from = document.getElementById('export-date-from').value;
                const to = document.getElementById('export-date-to').value;
                if (from) records = records.filter(r => (r.date || r.timestamp || '') >= from);
                if (to) records = records.filter(r => (r.date || r.timestamp || '') <= to + 'T23:59:59');
            }

            if (records.length === 0) {
                alert('Nenhum registro encontrado para os filtros selecionados.');
                return;
            }

            const exportData = {
                exported_at: new Date().toISOString(),
                section: _exportSection,
                total_records: records.length,
                records: records,
            };

            downloadJSON(exportData, `aegis_${_exportSection}_${new Date().toISOString().slice(0,10)}.json`);
            closeModal('modal-export');
        }

        async function exportConfig() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                const exportData = {
                    exported_at: new Date().toISOString(),
                    llm: {
                        base_url: data.llm.base_url,
                        model: data.llm.model,
                        api_key: '***',
                    },
                    broker: {
                        api_key: '***',
                        api_secret: '***',
                    },
                    trading: data.trading,
                };
                downloadJSON(exportData, `aegis_config_${new Date().toISOString().slice(0,10)}.json`);
            } catch(e) {
                alert('Erro ao exportar configuração: ' + e.message);
            }
        }

        function resetPrompt() {
            if (!confirm('Restaurar o prompt padrão? O prompt atual será perdido.')) return;
            // AC-C3-07: Build prompt from live config, not hardcoded values
            fetch('/api/settings').then(r => r.json()).then(cfg => {
                const t = cfg.trading || {};
                const cap = t.capital || 100;
                const risk = t.risk_per_trade_pct || 1;
                const maxPos = t.max_positions || 1;
                const minConf = (t.min_confidence || 0.5) * 100;
                const dailyLoss = t.max_daily_loss_pct || 5;
                const posSize = t.max_position_size_pct || 20;
                const defaultPrompt = `Você é um trader de swing trade de criptomoedas. Analise os dados de mercado e tome uma decisão de trading.

Dados de Mercado:
{market_state}

Portfólio Atual:
{portfolio}

Regras:
- Apenas LONG (sem SHORT)
- Máximo ${maxPos} posição(ões) por vez
- Risco de ${risk}% por trade
- Capital de referência: R$ ${cap}
- Stop loss obrigatório
- Take profit obrigatório
- Só opera se confiança >= ${minConf}%
- Perda diária máxima: ${dailyLoss}% do capital
- Tamanho máximo de posição: ${posSize}% do capital

Responda com JSON:
{
    "action": "LONG" ou "HOLD" ou "CLOSE",
    "confidence": 0.0 a 1.0,
    "thesis": "raciocínio breve",
    "entry_price": número ou null,
    "stop_loss": número ou null,
    "take_profit": número ou null,
    "reasoning": "análise detalhada"
}`;
                document.getElementById('trading-prompt').value = defaultPrompt;
            }).catch(() => {
                alert('Erro ao buscar configuração. Usando prompt mínimo.');
                document.getElementById('trading-prompt').value = 'Você é um trader. Analise os dados e responda com JSON.';
            });
        }
    </script>
</body>
</html>"""


# Legacy state removed — all state lives in worker._state (Decimal strings)


class TradeRequest(BaseModel):
    symbol: str
    action: str
    entry_price: float
    quantity: float
    stop_loss: float | None = None
    take_profit: float | None = None


class KillSwitchRequest(BaseModel):
    active: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Start autonomous worker
        from aegis.worker import get_worker
        import asyncio

        worker = get_worker()
        task = asyncio.create_task(worker.start())
        logger.info("Autonomous worker started")
        yield
        await worker.stop()
        task.cancel()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.include_router(settings_router)
    app.include_router(ws_router)

    @app.get("/", response_class=HTMLResponse)
    async def root() -> str:
        return DASHBOARD_HTML

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.trading_environment.value}

    @app.get("/health/ready")
    async def readiness_check() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        """Get current trading state from the worker."""
        from aegis.worker import get_worker
        worker = get_worker()
        return worker.state

    @app.post("/api/run")
    async def manual_run(_auth: None = Depends(require_api_key)) -> dict[str, str]:
        """Trigger a manual tick immediately. AC-C10-10: requires API key."""
        from aegis.worker import get_worker
        worker = get_worker()
        import asyncio as _aio
        _aio.create_task(worker._tick())
        return {"status": "ok", "message": "Tick executado manualmente"}

    @app.post("/api/trade")
    async def place_trade(request: TradeRequest, _auth: None = Depends(require_api_key)) -> dict[str, Any]:
        """AC-C10-10: Place a new trade order through the canonical pipeline. Requires API key.

        AC-ARCH-2: Frontend never calls broker directly.
        Frontend -> TradingPipeline -> RiskEngine -> ExecutionEngine -> Broker.
        """

        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction
        from aegis.pipeline import TradingPipeline

        pipeline = TradingPipeline()

        # Create decision contract
        action = TradingAction(request.action.upper())
        decision = DecisionContract(
            action=action,
            confidence=Decimal("0.85"),
            thesis=f"Manual trade: {request.symbol}",
            entry_price=Decimal(str(request.entry_price)),
            stop_loss=Decimal(str(request.stop_loss)) if request.stop_loss else None,
            take_profit=Decimal(str(request.take_profit)) if request.take_profit else None,
        )

        # Run through canonical pipeline (Risk -> Execution -> Portfolio -> Audit)
        result = await pipeline.run(symbol=request.symbol, decision=decision)

        if result.status == "REJECTED":
            return {"status": "error", "error": "; ".join(result.errors)}
        if result.status == "ERROR":
            return {"status": "error", "error": "; ".join(result.errors)}

        # Sync worker state from pipeline
        from aegis.worker import get_worker
        worker = get_worker()
        for pos in pipeline.state["positions"]:
            if pos["id"] not in [p.get("id") for p in worker.state.get("positions", [])]:
                worker._state["positions"].append(pos)
        for order in pipeline.state["orders"]:
            if order["id"] not in [o.get("id") for o in worker.state.get("orders", [])]:
                worker._state["orders"].append(order)

        order_id = result.order_result.order_id if result.order_result else str(uuid4())
        await broadcast({"type": "trade_completed", "status": result.status, "order_id": str(order_id)})

        return {"status": "ok", "order_id": str(order_id), "message": f"Trade {result.status}"}

    @app.post("/api/position/{position_id}/close")
    async def close_position(position_id: str, _auth: None = Depends(require_api_key)) -> dict[str, str]:
        """AC-C10-10: Close a position through the worker. Requires API key.

        Frontend -> Worker.close_position_manual -> Portfolio.close_position.
        """
        from aegis.worker import get_worker
        worker = get_worker()

        result = await worker.close_position_manual(position_id)

        if result["status"] == "NOT_FOUND":
            return {"status": "error", "error": result["error"]}
        if result["status"] == "ERROR":
            return {"status": "error", "error": result.get("error", "Close failed")}

        await broadcast({"type": "position_closed", "position_id": position_id})
        return {"status": "ok", "message": "Position closed", "pnl": result["pnl"]}

    @app.post("/api/risk/kill-switch")
    async def toggle_kill_switch(request: KillSwitchRequest, _auth: None = Depends(require_api_key)) -> dict[str, str]:
        """Toggle kill switch and wire to RiskEngine. Requires API key."""
        from aegis.worker import get_worker
        worker = get_worker()

        if request.active:
            worker.risk_engine.activate_kill_switch()
            await broadcast({"type": "kill_switch", "active": True})
            return {"status": "ok", "message": "Kill switch activated - all new orders blocked"}
        else:
            worker.risk_engine.deactivate_kill_switch()
            await broadcast({"type": "kill_switch", "active": False})
            return {"status": "ok", "message": "Kill switch deactivated"}

    return app


app = create_app()

import { useState, useEffect } from 'react'
import { AllSettings } from '../types'

export default function Config() {
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [llmStatus, setLlmStatus] = useState('')
  const [brokerStatus, setBrokerStatus] = useState('')
  const [tradingStatus, setTradingStatus] = useState('')

  useEffect(() => {
    fetch('/api/settings')
      .then(res => res.json())
      .then(data => setSettings(data))
      .catch(console.error)
  }, [])

  if (!settings) return <div className="empty">Carregando...</div>

  const saveLLM = async () => {
    await fetch('/api/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings.llm),
    })
    setLlmStatus('LLM salvo com sucesso')
  }

  const saveBroker = async () => {
    await fetch('/api/settings/broker', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings.broker),
    })
    setBrokerStatus('Broker salvo com sucesso')
  }

  const saveTrading = async () => {
    await fetch('/api/settings/trading', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings.trading),
    })
    setTradingStatus('Trading salvo com sucesso')
  }

  return (
    <div className="section active">
      <div className="card info">
        <h2>Mercado Bitcoin — API</h2>
        <div className="setup-box">
          <h3>Como obter suas API keys:</h3>
          1. Acesse <a href="https://www.mercadobitcoin.com.br/api-keys" target="_blank">mercadobitcoin.com.br/api-keys</a><br />
          2. Clique em "Criar nova API key"<br />
          3. Dê um nome (ex: "AEGIS Bot")<br />
          4. Permissões: marque <code>Leitura</code> e <code>Trade</code><br />
          5. Copie a <code>API Key</code> e o <code>Secret</code><br />
          6. Cole abaixo
        </div>
      </div>
      
      <div className="card">
        <h2>Broker — Mercado Bitcoin</h2>
        <div className="row">
          <div className="field">
            <label>API Key</label>
            <input type="password" placeholder="Cole sua API Key aqui" value={settings.broker.api_key} onChange={e => setSettings({...settings, broker: {...settings.broker, api_key: e.target.value}})} />
          </div>
          <div className="field">
            <label>API Secret</label>
            <input type="password" placeholder="Cole seu Secret aqui" value={settings.broker.api_secret} onChange={e => setSettings({...settings, broker: {...settings.broker, api_secret: e.target.value}})} />
          </div>
        </div>
        <button className="btn" onClick={saveBroker}>Salvar Broker</button>
        {brokerStatus && <div className="status ok">{brokerStatus}</div>}
      </div>
      
      <div className="card">
        <h2>LLM Provider</h2>
        <div className="field">
          <label>API Base URL</label>
          <input type="text" placeholder="https://api.openai.com/v1" value={settings.llm.base_url} onChange={e => setSettings({...settings, llm: {...settings.llm, base_url: e.target.value}})} />
          <div className="hint">URL da API do provider. Exemplos: OpenAI, Anthropic, Groq, Together, local (ollama)</div>
        </div>
        <div className="row">
          <div className="field">
            <label>Modelo</label>
            <input type="text" placeholder="gpt-4" value={settings.llm.model} onChange={e => setSettings({...settings, llm: {...settings.llm, model: e.target.value}})} />
          </div>
          <div className="field">
            <label>API Key</label>
            <input type="password" placeholder="sk-..." value={settings.llm.api_key} onChange={e => setSettings({...settings, llm: {...settings.llm, api_key: e.target.value}})} />
          </div>
        </div>
        <button className="btn" onClick={saveLLM}>Salvar LLM</button>
        {llmStatus && <div className="status ok">{llmStatus}</div>}
      </div>
      
      <div className="card">
        <h2>Trading</h2>
        <div className="field">
          <label>Pares de trading (separados por vírgula)</label>
          <input type="text" placeholder="BTC-BRL,ETH-BRL,SOL-BRL" value={settings.trading.symbols} onChange={e => setSettings({...settings, trading: {...settings.trading, symbols: e.target.value}})} />
        </div>
        <div className="row">
          <div className="field">
            <label>Timeframe</label>
            <select value={settings.trading.timeframe} onChange={e => setSettings({...settings, trading: {...settings.trading, timeframe: e.target.value}})}>
              <option value="1h">1 hora</option>
              <option value="4h">4 horas</option>
              <option value="1d">1 dia</option>
            </select>
          </div>
          <div className="field">
            <label>Ambiente</label>
            <select value={settings.trading.trading_environment} onChange={e => setSettings({...settings, trading: {...settings.trading, trading_environment: e.target.value}})}>
              <option value="SANDBOX">SANDBOX (simulação)</option>
              <option value="LIVE">LIVE (dinheiro real)</option>
            </select>
          </div>
        </div>
        <button className="btn" onClick={saveTrading}>Salvar Trading</button>
        {tradingStatus && <div className="status ok">{tradingStatus}</div>}
      </div>
      
      <div className="card danger">
        <h2>Modo LIVE</h2>
        <div className="field">
          <label>Habilitar trading com dinheiro real</label>
          <label className="toggle">
            <input type="checkbox" checked={settings.trading.live_enabled} onChange={e => {
              if (e.target.checked && !confirm('ATENÇÃO: Isso habilita trading com DINHEIRO REAL na Mercado Bitcoin.\n\nTem certeza?')) {
                return
              }
              setSettings({...settings, trading: {...settings.trading, live_enabled: e.target.checked}})
            }} />
            <span className="slider"></span>
          </label>
        </div>
      </div>
    </div>
  )
}

import { useState } from 'react'
import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function Risk({ state }: Props) {
  const [killActive, setKillActive] = useState(false)
  const [killStatus, setKillStatus] = useState('')

  const exposure = state.capital ? (state.exposure / state.capital * 100) : 0
  const drawdown = state.peak_equity ? Math.max(0, ((state.peak_equity - state.capital) / state.peak_equity) * 100) : 0

  const toggleKillSwitch = async () => {
    const res = await fetch('/api/risk/kill-switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: !killActive }),
    })
    const result = await res.json()
    setKillActive(!killActive)
    setKillStatus(result.message)
  }

  return (
    <div className="section active">
      <div className="card">
        <h2>Limits de Risco</h2>
        <div className="metrics">
          <div className="metric">
            <div className="value">R$ 100.00</div>
            <div className="label">Capital Ref.</div>
          </div>
          <div className="metric">
            <div className="value">R$ 1.00</div>
            <div className="label">Max por Trade (1%)</div>
          </div>
          <div className="metric">
            <div className="value">1</div>
            <div className="label">Max Posições</div>
          </div>
          <div className="metric">
            <div className="value">10%</div>
            <div className="label">Circuit Breaker</div>
          </div>
        </div>
        
        <h2>Exposição Atual</h2>
        <div style={{marginBottom: '12px'}}>
          <div style={{display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px'}}>
            <span>Exposição: {exposure.toFixed(1)}%</span>
            <span>Limite: 100%</span>
          </div>
          <div className="risk-bar">
            <div className={`fill ${exposure < 50 ? 'green' : exposure < 80 ? 'yellow' : 'red'}`} style={{width: `${exposure}%`}} />
          </div>
        </div>
        
        <h2>Drawdown Hoje</h2>
        <div>
          <div style={{display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px'}}>
            <span>Drawdown: {drawdown.toFixed(1)}%</span>
            <span>Limite: 10%</span>
          </div>
          <div className="risk-bar">
            <div className={`fill ${drawdown < 5 ? 'green' : drawdown < 8 ? 'yellow' : 'red'}`} style={{width: `${drawdown}%`}} />
          </div>
        </div>
      </div>
      
      <div className="card danger">
        <h2>Kill Switch</h2>
        <div className="field">
          <label>Ativar Kill Switch (bloqueia todas as ordens)</label>
          <label className="toggle">
            <input type="checkbox" checked={killActive} onChange={toggleKillSwitch} />
            <span className="slider"></span>
          </label>
        </div>
        {killStatus && <div className={`status ${killActive ? 'error' : 'ok'}`}>{killStatus}</div>}
      </div>
    </div>
  )
}

import { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import Positions from './components/Positions'
import Orders from './components/Orders'
import History from './components/History'
import AIDecisions from './components/AIDecisions'
import Risk from './components/Risk'
import Config from './components/Config'
import { useWebSocket } from './hooks/useWebSocket'
import { TradingState } from './types'

type Section = 'dashboard' | 'positions' | 'orders' | 'history' | 'ai' | 'risk' | 'config'

function App() {
  const [section, setSection] = useState<Section>('dashboard')
  const [state, setState] = useState<TradingState>({
    capital: 100,
    pnl: 0,
    positions: [],
    orders: [],
    history: [],
    decisions: [],
    exposure: 0,
    peak_equity: 100,
  })

  const ws = useWebSocket('/ws', (data) => {
    setState(prev => ({ ...prev, ...data }))
  })

  useEffect(() => {
    fetch('/api/state')
      .then(res => res.json())
      .then(data => setState(data))
      .catch(console.error)
  }, [])

  const renderSection = () => {
    switch (section) {
      case 'dashboard': return <Dashboard state={state} />
      case 'positions': return <Positions state={state} />
      case 'orders': return <Orders state={state} />
      case 'history': return <History state={state} />
      case 'ai': return <AIDecisions state={state} />
      case 'risk': return <Risk state={state} />
      case 'config': return <Config />
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>AEGIS <span className="env">SANDBOX</span></h1>
        <div className="header-right">
          <span className="clock">{new Date().toLocaleString('pt-BR')}</span>
          <button className="btn-small" onClick={() => setSection('config')}>Config</button>
        </div>
      </header>
      <nav className="nav">
        {(['dashboard', 'positions', 'orders', 'history', 'ai', 'risk', 'config'] as Section[]).map(s => (
          <button key={s} className={section === s ? 'active' : ''} onClick={() => setSection(s)}>
            {s === 'ai' ? 'IA Decisions' : s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </nav>
      <main className="container">
        {renderSection()}
      </main>
    </div>
  )
}

export default App

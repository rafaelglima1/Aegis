import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function Dashboard({ state }: Props) {
  const exposure = state.capital ? (state.exposure / state.capital * 100) : 0

  return (
    <div className="section active">
      <div className="metrics">
        <div className="metric">
          <div className="value">R$ {state.capital.toFixed(2)}</div>
          <div className="label">Capital Virtual</div>
        </div>
        <div className={`metric ${state.pnl >= 0 ? 'green' : 'red'}`}>
          <div className="value">R$ {state.pnl.toFixed(2)}</div>
          <div className="label">P&L Hoje</div>
        </div>
        <div className="metric">
          <div className="value">{state.positions.length}</div>
          <div className="label">Posições Abertas</div>
        </div>
        <div className="metric">
          <div className="value">{exposure.toFixed(1)}%</div>
          <div className="label">Exposição</div>
        </div>
      </div>
      
      <div className="row">
        <div className="card">
          <h2>Posições Abertas</h2>
          {state.positions.length === 0 ? (
            <div className="empty">Nenhuma posição aberta</div>
          ) : (
            state.positions.map(p => (
              <div key={p.id} className="ai-decision">
                <div className="header">
                  <span className="symbol">{p.symbol}</span>
                  <span className="action long">{p.side}</span>
                </div>
                <div className="thesis">Entrada: R$ {p.entry_price} | Atual: R$ {p.current_price || '--'}</div>
                <div className="confidence">P&L: R$ {(p.pnl || 0).toFixed(2)}</div>
              </div>
            ))
          )}
        </div>
        
        <div className="card">
          <h2>Últimas Decisões IA</h2>
          {state.decisions.length === 0 ? (
            <div className="empty">Nenhuma decisão ainda</div>
          ) : (
            state.decisions.slice(-3).reverse().map((d, i) => (
              <div key={i} className="ai-decision">
                <div className="header">
                  <span className="symbol">{d.symbol || '--'}</span>
                  <span className={`action ${d.action?.toLowerCase()}`}>{d.action}</span>
                </div>
                <div className="thesis">{d.thesis || ''}</div>
                <div className="confidence">Confiança: {(d.confidence * 100).toFixed(0)}%</div>
              </div>
            ))
          )}
        </div>
      </div>
      
      <div className="card">
        <h2>Equity Curve</h2>
        <div className="chart">Gráfico de equity será exibido aqui</div>
      </div>
    </div>
  )
}

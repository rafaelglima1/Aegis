import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function Positions({ state }: Props) {
  const closePosition = async (id: string) => {
    if (!confirm('Fechar esta posição?')) return
    await fetch(`/api/position/${id}/close`, { method: 'POST' })
  }

  return (
    <div className="section active">
      <div className="card">
        <h2>Posições Abertas</h2>
        <table className="positions-table">
          <thead>
            <tr>
              <th>Par</th>
              <th>Lado</th>
              <th>Qtd</th>
              <th>Preço Entrada</th>
              <th>Preço Atual</th>
              <th>P&L</th>
              <th>Stop</th>
              <th>Take Profit</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {state.positions.length === 0 ? (
              <tr><td colSpan={9} className="empty">Nenhuma posição aberta</td></tr>
            ) : (
              state.positions.map(p => (
                <tr key={p.id}>
                  <td><strong>{p.symbol}</strong></td>
                  <td><span className="badge long">{p.side}</span></td>
                  <td>{p.quantity}</td>
                  <td>R$ {p.entry_price}</td>
                  <td>R$ {p.current_price || '--'}</td>
                  <td style={{color: (p.pnl || 0) >= 0 ? '#00ff88' : '#ff4444'}}>
                    R$ {(p.pnl || 0).toFixed(2)}
                  </td>
                  <td>R$ {p.stop_loss || '--'}</td>
                  <td>R$ {p.take_profit || '--'}</td>
                  <td>
                    <button className="btn danger" style={{padding: '4px 8px', fontSize: '11px'}} onClick={() => closePosition(p.id)}>
                      Fechar
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

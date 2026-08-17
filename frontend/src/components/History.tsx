import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function History({ state }: Props) {
  return (
    <div className="section active">
      <div className="card">
        <h2>Histórico de Trades</h2>
        <table className="history-table">
          <thead>
            <tr>
              <th>Data</th>
              <th>Par</th>
              <th>Lado</th>
              <th>Qtd</th>
              <th>Entrada</th>
              <th>Saída</th>
              <th>P&L</th>
              <th>Fee</th>
            </tr>
          </thead>
          <tbody>
            {state.history.length === 0 ? (
              <tr><td colSpan={8} className="empty">Nenhum trade executado</td></tr>
            ) : (
              state.history.map((t, i) => (
                <tr key={i}>
                  <td>{t.date || '--'}</td>
                  <td>{t.symbol}</td>
                  <td><span className={`badge ${t.side?.toLowerCase()}`}>{t.side}</span></td>
                  <td>{t.quantity}</td>
                  <td>R$ {t.entry_price}</td>
                  <td>R$ {t.exit_price}</td>
                  <td style={{color: parseFloat(t.pnl) >= 0 ? '#00ff88' : '#ff4444'}}>
                    R$ {parseFloat(t.pnl).toFixed(2)}
                  </td>
                  <td>R$ {parseFloat(t.fee).toFixed(2)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

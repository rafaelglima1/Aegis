import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function AIDecisions({ state }: Props) {
  return (
    <div className="section active">
      <div className="card">
        <h2>Decisões da IA</h2>
        {state.decisions.length === 0 ? (
          <div className="empty">Nenhuma decisão registrada</div>
        ) : (
          state.decisions.map((d, i) => (
            <div key={i} className="ai-decision">
              <div className="header">
                <span className="symbol">{d.symbol || '--'} — {d.model || 'LLM'}</span>
                <span className={`action ${d.action?.toLowerCase()}`}>{d.action}</span>
              </div>
              <div className="thesis">{d.thesis || ''}</div>
              <div className="confidence">
                Confiança: {(d.confidence * 100).toFixed(0)}% | Provider: {d.provider || '--'}
              </div>
              <div style={{fontSize: '11px', color: '#666', marginTop: '4px'}}>
                {d.reasoning || ''}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

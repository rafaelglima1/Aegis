import { useState } from 'react'
import { TradingState } from '../types'

interface Props {
  state: TradingState
}

export default function Orders({ state }: Props) {
  const [symbol, setSymbol] = useState('BTC-BRL')
  const [action, setAction] = useState('LONG')
  const [price, setPrice] = useState('')
  const [qty, setQty] = useState('')
  const [stop, setStop] = useState('')
  const [tp, setTp] = useState('')
  const [status, setStatus] = useState('')

  const placeOrder = async () => {
    if (!price || !qty) {
      setStatus('error: Preço e quantidade são obrigatórios')
      return
    }

    const res = await fetch('/api/trade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol,
        action,
        entry_price: parseFloat(price),
        quantity: parseFloat(qty),
        stop_loss: stop ? parseFloat(stop) : null,
        take_profit: tp ? parseFloat(tp) : null,
      }),
    })
    const result = await res.json()
    setStatus(result.status === 'ok' ? 'ok: Ordem enviada' : `error: ${result.error}`)
  }

  return (
    <div className="section active">
      <div className="card">
        <h2>Nova Ordem</h2>
        <div className="row">
          <div className="field">
            <label>Par</label>
            <select value={symbol} onChange={e => setSymbol(e.target.value)}>
              <option>BTC-BRL</option>
              <option>ETH-BRL</option>
              <option>SOL-BRL</option>
            </select>
          </div>
          <div className="field">
            <label>Ação</label>
            <select value={action} onChange={e => setAction(e.target.value)}>
              <option value="LONG">LONG (Comprar)</option>
              <option value="CLOSE">CLOSE (Vender)</option>
            </select>
          </div>
        </div>
        <div className="row">
          <div className="field">
            <label>Preço Entrada</label>
            <input type="number" step="0.01" placeholder="0.00" value={price} onChange={e => setPrice(e.target.value)} />
          </div>
          <div className="field">
            <label>Quantidade</label>
            <input type="number" step="0.0001" placeholder="0.0000" value={qty} onChange={e => setQty(e.target.value)} />
          </div>
        </div>
        <div className="row">
          <div className="field">
            <label>Stop Loss</label>
            <input type="number" step="0.01" placeholder="0.00" value={stop} onChange={e => setStop(e.target.value)} />
          </div>
          <div className="field">
            <label>Take Profit</label>
            <input type="number" step="0.01" placeholder="0.00" value={tp} onChange={e => setTp(e.target.value)} />
          </div>
        </div>
        <button className="btn" onClick={placeOrder}>Enviar Ordem</button>
        {status && <div className={`status ${status.startsWith('ok') ? 'ok' : 'error'}`}>{status}</div>}
      </div>
      
      <div className="card">
        <h2>Ordens Ativas</h2>
        <table className="orders-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Par</th>
              <th>Lado</th>
              <th>Qtd</th>
              <th>Preço</th>
              <th>Status</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {state.orders.length === 0 ? (
              <tr><td colSpan={7} className="empty">Nenhuma ordem ativa</td></tr>
            ) : (
              state.orders.map(o => (
                <tr key={o.id}>
                  <td style={{fontFamily: 'monospace', fontSize: '11px'}}>{o.id.slice(0, 8)}</td>
                  <td>{o.symbol}</td>
                  <td><span className={`badge ${o.side?.toLowerCase()}`}>{o.side}</span></td>
                  <td>{o.quantity}</td>
                  <td>R$ {o.price}</td>
                  <td><span className={`badge ${o.status?.toLowerCase()}`}>{o.status}</span></td>
                  <td>{o.timestamp || '--'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

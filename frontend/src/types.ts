export interface Position {
  id: string
  symbol: string
  side: string
  quantity: string
  entry_price: string
  current_price?: string
  pnl?: number
  stop_loss?: string
  take_profit?: string
  status: string
  opened_at?: string
}

export interface Order {
  id: string
  symbol: string
  side: string
  quantity: string
  price: string
  status: string
  timestamp?: string
}

export interface Trade {
  date: string
  symbol: string
  side: string
  quantity: string
  entry_price: string
  exit_price: string
  pnl: string
  fee: string
}

export interface AIDecision {
  symbol: string
  action: string
  confidence: number
  thesis: string
  provider?: string
  model?: string
  reasoning?: string
  timestamp?: string
}

export interface TradingState {
  capital: number
  pnl: number
  positions: Position[]
  orders: Order[]
  history: Trade[]
  decisions: AIDecision[]
  exposure: number
  peak_equity: number
}

export interface LLMSettings {
  base_url: string
  api_key: string
  model: string
}

export interface BrokerSettings {
  api_key: string
  api_secret: string
}

export interface TradingSettings {
  trading_environment: string
  live_enabled: boolean
  symbols: string
  timeframe: string
  timeframes: string
  capital: number
  risk_per_trade_pct: number
  max_positions: number
  circuit_breaker_pct: number
  long_only: boolean
  leverage: number
  instrument: string
}

export interface AllSettings {
  llm: LLMSettings
  broker: BrokerSettings
  trading: TradingSettings
}

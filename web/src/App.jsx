import { useEffect, useState } from 'react'
import './App.css'

const PAPER_SUMMARY_URL = import.meta.env.VITE_PAPER_SUMMARY_URL
const TRBOT_FUNCTION_URL = import.meta.env.VITE_TRBOT_FUNCTION_URL || 'https://func-3qs3shmnmkj5m.azurewebsites.net'
const CRYPTO_SCAN_URL = import.meta.env.VITE_CRYPTO_SCAN_URL || 'https://func-coinbase-trader-v2.azurewebsites.net/api/crypto-scan'

const tabs = [
  { id: 'paper', label: 'Paper P/L' },
  { id: 'analyze', label: 'Analyze Ticker' },
  { id: 'research', label: 'Research Summary' },
  { id: 'scalp', label: 'Crypto Scalp' },
]

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

const percent = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
})

function formatCurrency(value) {
  return currency.format(Number(value || 0))
}

function formatPercent(value) {
  return `${percent.format(Number(value || 0))}%`
}

function formatNumber(value, digits = 2) {
  const number = Number(value || 0)
  return number.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function formatDateTime(value) {
  if (!value) return 'Waiting'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function pnlClass(value) {
  const number = Number(value || 0)
  if (number > 0) return 'positive'
  if (number < 0) return 'negative'
  return 'neutral'
}

function App() {
  const [activeTab, setActiveTab] = useState('paper')
  const [apiKey, setApiKey] = useState(() => window.localStorage.getItem('trbot_api_key') || '')
  const [settingsOpen, setSettingsOpen] = useState(false)

  function saveApiKey(nextKey) {
    setApiKey(nextKey)
    window.localStorage.setItem('trbot_api_key', nextKey)
    setSettingsOpen(false)
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Trading Research Bot</p>
          <h1>Coinbase Breakout Desk</h1>
        </div>
        <button type="button" className="settings-button" onClick={() => setSettingsOpen(true)}>
          API Settings
        </button>
      </header>

      <nav className="tabs" aria-label="Dashboard pages">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={activeTab === tab.id ? 'active' : ''}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === 'paper' && <PaperTradingDashboard />}
      {activeTab === 'analyze' && <TickerAnalyzer apiKey={apiKey} functionUrl={TRBOT_FUNCTION_URL} onOpenSettings={() => setSettingsOpen(true)} />}
      {activeTab === 'research' && <ResearchSummary apiKey={apiKey} functionUrl={TRBOT_FUNCTION_URL} onOpenSettings={() => setSettingsOpen(true)} />}
      {activeTab === 'scalp' && <CryptoScalp scanUrl={CRYPTO_SCAN_URL} />}

      {settingsOpen && <SettingsModal apiKey={apiKey} onSave={saveApiKey} onClose={() => setSettingsOpen(false)} />}
    </main>
  )
}

function PaperTradingDashboard() {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState('')

  async function loadSummary() {
    if (!PAPER_SUMMARY_URL) {
      setStatus('missing-config')
      setError('VITE_PAPER_SUMMARY_URL is not configured for this build.')
      return
    }

    try {
      setStatus('loading')
      setError('')
      const response = await fetch(PAPER_SUMMARY_URL, { cache: 'no-store' })
      const payload = await response.json()

      if (!response.ok || payload.error) {
        throw new Error(payload.error || `Summary API returned ${response.status}`)
      }

      setData(payload)
      setStatus('ready')
    } catch (caught) {
      setStatus('error')
      setError(caught instanceof Error ? caught.message : 'Unable to load summary.')
    }
  }

  useEffect(() => {
    const timerId = window.setTimeout(loadSummary, 0)
    return () => window.clearTimeout(timerId)
  }, [])

  const summary = data?.summary || {}
  const openPositions = data?.open_positions || []
  // Show the complete daily history, newest first. Sort explicitly so the view
  // is independent of the API's ordering.
  const dailyRows = [...(data?.daily || [])].sort((a, b) => (a.date < b.date ? 1 : -1))
  const recentTrades = data?.recent_closed_trades || []
  const isLoading = status === 'loading'

  return (
    <>
      <div className="utility-row">
        <div className="status-panel">
          <span className={`status-dot ${status}`}></span>
          <span>{status === 'ready' ? `Updated ${formatDateTime(data?.generated_at_utc)}` : status.replace('-', ' ')}</span>
          <button type="button" onClick={loadSummary} disabled={isLoading}>
            {isLoading ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="alert">{error}</div>}

      <section className="scoreboard" aria-label="Profit and loss summary">
        <Metric label="Total P/L" value={formatCurrency(summary.total_pnl_usd)} tone={pnlClass(summary.total_pnl_usd)} />
        <Metric label="Realized" value={formatCurrency(summary.realized_pnl_usd)} tone={pnlClass(summary.realized_pnl_usd)} />
        <Metric label="Unrealized" value={formatCurrency(summary.unrealized_pnl_usd)} tone={pnlClass(summary.unrealized_pnl_usd)} />
        <Metric label="Allocated" value={formatCurrency(summary.allocated_usd)} />
        <Metric label="Open" value={summary.open_positions ?? 0} />
        <Metric label="Win Rate" value={formatPercent(summary.win_rate_pct)} />
      </section>

      <section className="grid-two">
        <Panel title="Open Positions" meta={`${openPositions.length} active`}>
          {openPositions.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Coin</th>
                    <th>Strategy</th>
                    <th>Entry</th>
                    <th>Mark</th>
                    <th>P/L</th>
                    <th>Trail</th>
                    <th>Take Profit</th>
                  </tr>
                </thead>
                <tbody>
                  {openPositions.map((position) => (
                    <tr key={position.product_id}>
                      <td className="strong">{position.product_id}</td>
                      <td>{position.strategy || 'Scanner'}</td>
                      <td>{formatCurrency(position.entry_price)}</td>
                      <td>{formatCurrency(position.mark_price)}</td>
                      <td className={pnlClass(position.unrealized_pnl_usd)}>
                        {formatCurrency(position.unrealized_pnl_usd)} <small>{formatPercent(position.unrealized_pnl_pct)}</small>
                      </td>
                      <td>{formatCurrency(position.current_trailing_stop)}</td>
                      <td>{formatCurrency(position.take_profit_boundary)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="No open positions" text="Bot is scanning every 5 min. It enters when score ≥ 70/100 + liquidity ($1M vol) + OBV pressure + BTC regime pass. Check Crypto Scalp tab for live scores." />
          )}
        </Panel>

        <Panel title="Daily P/L" meta={`${dailyRows.length} days`}>
          {dailyRows.length ? (
            <div className="daily-list">
              {dailyRows.map((day) => (
                <div className="daily-row" key={day.date}>
                  <span>{day.date}</span>
                  <span>{(day.closed_trades || 0) + (day.partial_takes || 0)} trades</span>
                  <strong className={pnlClass(day.realized_pnl_usd)}>{formatCurrency(day.realized_pnl_usd)}</strong>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No closed trades yet" text="Daily P/L builds after each trade exits via take-profit or trailing stop. Open positions and live scores show on the Crypto Scalp tab." />
          )}
        </Panel>
      </section>

      <Panel title="Recent Closed Trades" meta={`${recentTrades.length} shown`}>
        {recentTrades.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Coin</th>
                  <th>Exit Time</th>
                  <th>Reason</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>P/L</th>
                </tr>
              </thead>
              <tbody>
                {recentTrades.map((trade, index) => {
                  const pnl = trade.performance?.pnl_usd
                  return (
                    <tr key={`${trade.product_id}-${trade.exit?.timestamp || index}`}>
                      <td className="strong">{trade.product_id}</td>
                      <td>{formatDateTime(trade.exit?.timestamp)}</td>
                      <td>{trade.exit?.reason || 'Closed'}</td>
                      <td>{formatCurrency(trade.entry?.price)}</td>
                      <td>{formatCurrency(trade.exit?.price)}</td>
                      <td className={pnlClass(pnl)}>{formatCurrency(pnl)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No trades closed yet" text="The bot is live and scanning. All entry gates (score, liquidity, OBV, BTC regime) must pass simultaneously. Trades appear here once they close." />
        )}
      </Panel>
    </>
  )
}

function TickerAnalyzer({ apiKey, functionUrl, onOpenSettings }) {
  const [symbol, setSymbol] = useState('')
  const [analysis, setAnalysis] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSearch(event) {
    event.preventDefault()
    if (!apiKey) {
      onOpenSettings()
      return
    }
    if (!symbol.trim()) return

    try {
      setLoading(true)
      setError('')
      setAnalysis(null)
      const response = await fetch(`${functionUrl}/api/analyze/${encodeURIComponent(symbol.trim().toUpperCase())}?code=${apiKey}`)
      if (!response.ok) throw new Error(`AnalyzeTicker returned ${response.status}`)
      setAnalysis(await response.json())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to analyze ticker.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="tool-page">
      <Panel title="Analyze Any Ticker" meta="Stocks and crypto">
        <form className="search-form" onSubmit={handleSearch}>
          <input
            type="text"
            placeholder="BTC-USD, NVDA, ETH-USD, AAPL"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            autoComplete="off"
          />
          <button type="submit" disabled={loading || !symbol.trim()}>{loading ? 'Analyzing' : 'Analyze'}</button>
        </form>
        {!apiKey && <InlineNotice action={onOpenSettings} text="Add the trbot Function key to use stock and ticker analysis." />}
        {error && <div className="alert compact">{error}</div>}
      </Panel>

      {analysis && (
        <>
          <section className="analysis-hero">
            <div>
              <p className="eyebrow">{analysis.assetClass}</p>
              <h2>{analysis.symbol}</h2>
            </div>
            <strong>{formatCurrency(analysis.price)}</strong>
          </section>

          <section className="cards-three">
            <StyleCard title="Scalp 15m/4H" rec={analysis.recommendations?.scalp} />
            <StyleCard title="Swing 4H/Daily" rec={analysis.recommendations?.swing} />
            <StyleCard title="Long-Term Daily" rec={analysis.recommendations?.longTerm} />
          </section>

          <IndicatorPanel indicators={analysis.indicators} />
        </>
      )}
    </section>
  )
}

function ResearchSummary({ apiKey, functionUrl, onOpenSettings }) {
  return (
    <RunReport
      apiKey={apiKey}
      functionUrl={functionUrl}
      onOpenSettings={onOpenSettings}
      endpoint="run"
      title="Research Summary"
      description="Runs the stock and crypto research pass and returns candidates, scores, buy ranges, stops, targets, and rejected names."
      resultKey="candidates"
      emptyTitle="No candidates returned"
    />
  )
}

function CryptoScalp({ scanUrl }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function load(refresh = false) {
    try {
      setLoading(true)
      setError('')
      const url = refresh ? `${scanUrl}?refresh=1` : scanUrl
      const response = await fetch(url, { method: 'GET', cache: 'no-store' })
      if (!response.ok) throw new Error(`Crypto scan returned ${response.status}`)
      setReport(await response.json())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load crypto scan.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const timerId = window.setTimeout(() => load(false), 0)
    return () => window.clearTimeout(timerId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanUrl])

  const setups = report?.setups || []
  const positions = report?.positions || []
  const ready = setups.filter((row) => row.eligible)
  const watch = setups.filter((row) => !row.eligible)

  return (
    <section className="tool-page">
      <Panel
        title="Crypto Scalp Summary"
        meta={report ? `Generated ${formatDateTime(report.generated_at_utc)}` : 'Live Coinbase scan'}
      >
        <div className="report-intro">
          <p>
            Live Coinbase scan using the same strategy stack the paper engine trades on
            (consensus scoring across candle breakout, opening-range breakout, Bollinger
            reversal, 24h momentum and descending wedge, gated by liquidity and OBV).
          </p>
          <button type="button" onClick={() => load(true)} disabled={loading}>
            {loading ? 'Scanning…' : 'Refresh'}
          </button>
        </div>
        {error && <div className="alert compact">{error}</div>}
      </Panel>

      {report && (
        <>
          <section className="scoreboard report-metrics">
            <Metric label="Strategy" value={report.strategy || 'CoinbaseConsensus'} />
            <Metric label="Open positions" value={positions.length} />
            <Metric label="Ready to buy" value={report.ready_count ?? ready.length} />
            <Metric label="Universe" value={report.universe_size ?? setups.length} />
          </section>

          {positions.length > 0 && (
            <Panel title="Open Positions" meta={`${positions.length} currently held`}>
              <PositionsTable rows={positions} />
            </Panel>
          )}

          <Panel title="Ready Setups" meta={`${ready.length} meet all gates`}>
            {ready.length ? (
              <ScalpTable rows={ready} showReason={false} />
            ) : (
              <EmptyState title="No ready setups right now" text="No coin currently clears every entry gate. The strongest watch names are below." />
            )}
          </Panel>

          {watch.length > 0 && (
            <Panel title="Watchlist" meta={`${watch.length} names ranked by score`}>
              <ScalpTable rows={watch} showReason={true} />
            </Panel>
          )}
        </>
      )}
    </section>
  )
}

function PositionsTable({ rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Entry Price</th>
            <th>Current Price</th>
            <th>Unrealized P&amp;L</th>
            <th>Allocated</th>
            <th>Trail %</th>
            <th>Stop</th>
            <th>Partial TP</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((pos, i) => {
            const pnlClass = pos.unrealized_pnl_pct > 0 ? 'positive' : pos.unrealized_pnl_pct < 0 ? 'negative' : ''
            return (
              <tr key={`${pos.symbol}-${i}`}>
                <td className="strong">{pos.symbol}</td>
                <td>{formatCurrency(pos.entry_price)}</td>
                <td>{pos.current_price > 0 ? formatCurrency(pos.current_price) : '—'}</td>
                <td className={pnlClass}>
                  {pos.current_price > 0
                    ? `${pos.unrealized_pnl_pct > 0 ? '+' : ''}${pos.unrealized_pnl_pct.toFixed(2)}% ($${pos.unrealized_pnl_usd > 0 ? '+' : ''}${pos.unrealized_pnl_usd.toFixed(2)})`
                    : '—'}
                </td>
                <td>${(pos.allocated_usd || 0).toFixed(2)}</td>
                <td>{pos.trail_pct.toFixed(1)}%</td>
                <td>{pos.current_stop > 0 ? formatCurrency(pos.current_stop) : '—'}</td>
                <td>{pos.partial_taken ? <span className="badge positive">Done</span> : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ScalpTable({ rows, showReason }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Score</th>
            <th>Conf.</th>
            <th>Strategy</th>
            <th>Price</th>
            <th>Stop</th>
            <th>Targets</th>
            {showReason ? <th>Gate</th> : <th>Confirmed by</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const tierLabel = row.eligible ? null : row.liquidity_ok === false ? 'Small-cap' : 'Liquid'
            const tierClass = tierLabel === 'Liquid' ? 'neutral' : 'muted'
            const reasonShort = (row.reason || '')
              .replace(/24h dollar volume \$[\d,]+ < \$[\d,]+/g, 'low volume')
              .replace(/breakout candle volume \$[\d,]+ < \$[\d,]+/g, 'thin breakout')
              .replace(/OBV pressure [-\d.]+% < [-\d.]+%/g, 'weak OBV')
            return (
              <tr key={`${row.symbol}-${index}`}>
                <td className="strong">
                  {row.symbol}
                  {tierLabel && <span className={`badge ${tierClass}`}>{tierLabel}</span>}
                </td>
                <td>{formatNumber(row.score, 1)}{row.consensus_bonus > 0 ? ` (+${formatNumber(row.consensus_bonus, 0)})` : ''}</td>
                <td>{row.confidence_level}</td>
                <td>{String(row.strategy || '').replace(/_/g, ' ')}</td>
                <td>{formatCurrency(row.price)}</td>
                <td>{formatCurrency(row.stop_loss)}</td>
                <td>{formatCurrency(row.target1)} / {formatCurrency(row.target2)}</td>
                {showReason ? (
                  <td>{reasonShort || 'Filtered'}</td>
                ) : (
                  <td>{(row.confirming_strategies || []).map((s) => String(s).replace(/_/g, ' ')).join(', ') || row.strategy}</td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RunReport({ apiKey, functionUrl, onOpenSettings, endpoint, title, description, resultKey, emptyTitle }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function runReport() {
    if (!apiKey) {
      onOpenSettings()
      return
    }

    try {
      setLoading(true)
      setError('')
      const response = await fetch(`${functionUrl}/api/${endpoint}?code=${apiKey}`, { method: 'GET', cache: 'no-store' })
      if (!response.ok) throw new Error(`${title} returned ${response.status}`)
      setReport(await response.json())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Failed to load ${title.toLowerCase()}.`)
    } finally {
      setLoading(false)
    }
  }

  const rows = report?.[resultKey] || []
  const rejected = report?.rejected || []
  const movers = report?.screenedMovers || []

  return (
    <section className="tool-page">
      <Panel title={title} meta={report ? `Generated ${formatDateTime(report.generatedAtUtc)}` : 'Manual refresh'}>
        <div className="report-intro">
          <p>{description}</p>
          <button type="button" onClick={runReport} disabled={loading}>{loading ? 'Running' : 'Run Now'}</button>
        </div>
        {!apiKey && <InlineNotice action={onOpenSettings} text="Add the trbot Function key to run this summary." />}
        {error && <div className="alert compact">{error}</div>}
      </Panel>

      {report && (
        <>
          <section className="scoreboard report-metrics">
            <Metric label="Strategy" value={report.strategy || 'Research'} />
            <Metric label="Count" value={report.count ?? rows.length} />
            <Metric label="Generated" value={formatDateTime(report.generatedAtUtc)} />
          </section>

          {movers.length > 0 && (
            <Panel title="Screened Movers" meta={`${movers.length} movers`}>
              <SummaryTable rows={movers} mode="movers" />
            </Panel>
          )}

          <Panel title="Candidates" meta={`${rows.length} shown`}>
            {rows.length ? <SummaryTable rows={rows} mode="candidates" /> : <EmptyState title={emptyTitle} text="The function completed, but no names met the current filters." />}
          </Panel>

          {rejected.length > 0 && (
            <Panel title="Rejected Watchlist" meta={`${rejected.length} names`}>
              <SummaryTable rows={rejected} mode="rejected" />
            </Panel>
          )}
        </>
      )}
    </section>
  )
}

function SummaryTable({ rows, mode }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            {mode !== 'movers' && <th>Score</th>}
            {mode !== 'movers' && <th>Tier</th>}
            {mode === 'movers' && <th>Price</th>}
            {mode === 'movers' && <th>Change</th>}
            {mode !== 'movers' && <th>Buy Range</th>}
            {mode !== 'movers' && <th>Stop</th>}
            {mode !== 'movers' && <th>Targets</th>}
            {mode === 'rejected' && <th>Reason</th>}
            {mode !== 'rejected' && mode !== 'movers' && <th>Signals</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.symbol}-${index}`}>
              <td className="strong">{row.symbol}</td>
              {mode !== 'movers' && <td>{formatNumber(row.score, 1)}</td>}
              {mode !== 'movers' && <td>{row.tierLabel || row.tier || row.conviction || 'Candidate'}</td>}
              {mode === 'movers' && <td>{formatCurrency(row.price)}</td>}
              {mode === 'movers' && <td className={pnlClass(row.changePct)}>{formatPercent(row.changePct)}</td>}
              {mode !== 'movers' && <td>{formatCurrency(row.buyRangeLow)} - {formatCurrency(row.buyRangeHigh)}</td>}
              {mode !== 'movers' && <td>{formatCurrency(row.stopLoss)}</td>}
              {mode !== 'movers' && <td>{formatCurrency(row.target1)} / {formatCurrency(row.target2)}</td>}
              {mode === 'rejected' && <td>{row.reason || 'Filtered'}</td>}
              {mode !== 'rejected' && mode !== 'movers' && <td>{[...(row.signals || []), ...(row.patterns || [])].slice(0, 3).join(', ') || 'Setup'}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function IndicatorPanel({ indicators }) {
  if (!indicators) return null
  const rows = Object.values(indicators).filter(Boolean)

  return (
    <Panel title="Indicator Summary" meta={`${rows.length} timeframes`}>
      <div className="indicator-grid">
        {rows.map((item) => (
          <article className="indicator-card" key={item.timeframe}>
            <strong>{item.timeframe}</strong>
            <span>RSI {formatNumber(item.momentum?.rsi14, 1)}</span>
            <span>ADX {formatNumber(item.trend?.adx14, 1)}</span>
            <span>Conviction {formatNumber(item.conviction, 1)}</span>
            <span>Support {formatCurrency(item.levels?.support)}</span>
            <span>Resistance {formatCurrency(item.levels?.resistance)}</span>
          </article>
        ))}
      </div>
    </Panel>
  )
}

function StyleCard({ title, rec }) {
  if (!rec || !rec.available) {
    return <EmptyState title={title} text="Not enough data for this style yet." />
  }

  return (
    <article className={`style-card ${String(rec.bias || '').toLowerCase()}`}>
      <div className="style-card-title">
        <h3>{title}</h3>
        <span>{rec.bias || 'Neutral'} {rec.strength ? `(${rec.strength})` : ''}</span>
      </div>
      {rec.levels && (
        <div className="level-grid">
          <Metric label="Entry" value={formatCurrency(rec.levels.entry)} />
          <Metric label="Stop" value={formatCurrency(rec.levels.stopLoss)} tone="negative" />
          <Metric label="Target 1" value={formatCurrency(rec.levels.target1)} tone="positive" />
          <Metric label="Target 2" value={formatCurrency(rec.levels.target2)} tone="positive" />
          <Metric label="Risk" value={formatPercent(rec.levels.riskPct)} />
          <Metric label="R:R" value={`${formatNumber(rec.levels.rewardRisk, 1)}:1`} />
        </div>
      )}
      {rec.signals?.length > 0 && (
        <ul className="signal-list">
          {rec.signals.slice(0, 5).map((signal, index) => <li key={index}>{signal}</li>)}
        </ul>
      )}
    </article>
  )
}

function SettingsModal({ apiKey, onSave, onClose }) {
  const [draft, setDraft] = useState(apiKey)

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="API settings">
      <div className="modal-card">
        <div className="panel-title modal-title">
          <h2>API Settings</h2>
          <button type="button" className="ghost-button" onClick={onClose}>Close</button>
        </div>
        <div className="modal-body">
          <label htmlFor="api-key">trbot Function key</label>
          <input
            id="api-key"
            type="password"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Paste the AnalyzeTicker function key"
          />
          <p>The key is stored only in this browser local storage and is used for Analyze Ticker, Research Summary, and Crypto Scalp.</p>
          <button type="button" onClick={() => onSave(draft.trim())}>Save Key</button>
        </div>
      </div>
    </div>
  )
}

function InlineNotice({ text, action }) {
  return (
    <div className="inline-notice">
      <span>{text}</span>
      <button type="button" onClick={action}>Open Settings</button>
    </div>
  )
}

function Metric({ label, value, tone = 'neutral' }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </article>
  )
}

function Panel({ title, meta, children }) {
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>{title}</h2>
        <span>{meta}</span>
      </div>
      {children}
    </section>
  )
}

function EmptyState({ title, text }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  )
}

export default App

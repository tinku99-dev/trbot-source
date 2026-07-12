import { useEffect, useState } from 'react'
import './App.css'

const PAPER_SUMMARY_URL = import.meta.env.VITE_PAPER_SUMMARY_URL
const PAPER_CLOSE_URL = import.meta.env.VITE_PAPER_CLOSE_URL
  || PAPER_SUMMARY_URL?.replace(/\/summary(?:\?.*)?$/, '/close')
  || 'https://func-coinbase-trader-v2.azurewebsites.net/api/paper-trading/close'
const TRBOT_FUNCTION_URL = import.meta.env.VITE_TRBOT_FUNCTION_URL || 'https://func-3qs3shmnmkj5m.azurewebsites.net'
const CRYPTO_SCAN_URL = import.meta.env.VITE_CRYPTO_SCAN_URL || 'https://func-coinbase-trader-v2.azurewebsites.net/api/crypto-scan'

const tabs = [
  { id: 'paper', label: 'Paper P/L' },
  { id: 'analyze', label: 'Analyze Ticker' },
  { id: 'research', label: 'Research Summary' },
  { id: 'scalp', label: 'Crypto Scalp' },
]

const strategyPlaybook = [
  {
    title: 'Breakout Retest',
    bestFor: 'Trend continuation after a clean range break',
    confirms: ['Close above resistance', 'Retest holds support', 'Volume expands', 'OBV accumulation'],
    avoid: ['Buying the first wick', 'Retest closes below support', 'Breakout without volume'],
  },
  {
    title: 'Opening Range Breakout',
    bestFor: 'Early session momentum in liquid names',
    confirms: ['Range high breaks', 'Price stays above VWAP', 'ADX confirms trend', 'BTC or market regime agrees'],
    avoid: ['Chop inside the range', 'Weak relative strength', 'Immediate reversal back into range'],
  },
  {
    title: 'Bollinger Reversal',
    bestFor: 'Snapback from exhaustion lows',
    confirms: ['Lower-band pierce', 'RSI or MFI recovering', 'Close back inside bands', 'Support nearby'],
    avoid: ['Falling knife with no reclaim', 'Heavy distribution volume', 'Below major moving averages'],
  },
  {
    title: 'Momentum Runner',
    bestFor: 'High-liquidity coins or stocks already moving with participation',
    confirms: ['24h or daily move is strong', 'Recent dollar volume is high', 'Higher lows continue', 'Trailing stop has room'],
    avoid: ['Thin volume spikes', 'Overextended candle far above support', 'Late entry after parabolic move'],
  },
]

const indicatorGuide = [
  {
    name: 'ADX',
    use: 'Trend strength filter',
    good: 'Above 20-25 supports trend trades; rising ADX helps confirm breakouts.',
    caution: 'Lagging indicator, so it should confirm rather than trigger alone.',
  },
  {
    name: 'RSI / Stochastic',
    use: 'Momentum and exhaustion',
    good: 'RSI rising from the 40-60 zone can support continuation; oversold reclaim can support reversals.',
    caution: 'Overbought can stay overbought in strong trends.',
  },
  {
    name: 'OBV / Volume',
    use: 'Accumulation and participation',
    good: 'OBV pressure and up-volume ratio help separate real demand from thin candles.',
    caution: 'Volume spikes near highs can also mean distribution.',
  },
  {
    name: 'VWAP / 200 SMA',
    use: 'Fair value and regime',
    good: 'Price above VWAP or 200 SMA favors long setups and helps avoid weak names.',
    caution: 'A single reclaim is less useful without volume and structure.',
  },
  {
    name: 'ATR',
    use: 'Volatility-based stops',
    good: 'Stops and missed-breakout cancels should expand for volatile symbols.',
    caution: 'Too much ATR room can make risk too large for small accounts.',
  },
  {
    name: 'MFI / CCI',
    use: 'Volume-weighted momentum',
    good: 'Useful for spotting stronger reversals when price and money flow recover together.',
    caution: 'Works best with support/resistance, not as a standalone signal.',
  },
]

const lottoOptionsRules = [
  'Use only small premium that can go to zero.',
  'Prefer liquid contracts with tight bid/ask, open interest, and visible volume.',
  'Look for 5-20 DTE when trading a near-term catalyst; use more time for swing ideas.',
  'Use calls only when trend, volume, and breakout/retest agree; use puts only for confirmed breakdowns.',
  'Avoid contracts after IV has already exploded unless the move still has a clear catalyst.',
  'Take partial profits quickly because theta decay is working against the buyer.',
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

/**
 * Smart price formatter: 5 decimal places for sub-$1 crypto, 4 for $1-$100,
 * 2 for large prices. Prevents coins like FARTCOIN ($0.17500) and ALLO
 * ($0.37090) from losing precision.
 */
function formatPrice(value) {
  const num = Number(value || 0)
  if (num === 0) return '$0.00'
  const abs = Math.abs(num)
  const digits = abs >= 100 ? 2 : abs >= 1 ? 4 : 5
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(num)
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
  const [closeKey, setCloseKey] = useState(() => window.localStorage.getItem('paper_close_function_key') || '')
  const [closeStatus, setCloseStatus] = useState('')
  const [closingId, setClosingId] = useState('')

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

  function saveCloseKey(nextKey) {
    setCloseKey(nextKey)
    window.localStorage.setItem('paper_close_function_key', nextKey)
  }

  async function closePosition(productId) {
    if (!PAPER_CLOSE_URL) {
      setCloseStatus('Close endpoint is not configured for this build.')
      return
    }

    if (!productId) {
      setCloseStatus('Choose an active coin row to close.')
      return
    }

    const key = closeKey.trim()
    if (!key) {
      setCloseStatus('Add the Function key before closing a paper position.')
      return
    }

    const target = productId
    const url = new URL(PAPER_CLOSE_URL, window.location.origin)
    url.searchParams.set('productId', productId)

    try {
      setClosingId(target)
      setCloseStatus(`Closing ${productId}...`)
      const response = await fetch(url.toString(), {
        method: 'POST',
        headers: { 'x-functions-key': key },
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || payload.error) {
        throw new Error(payload.message || payload.error || `Close API returned ${response.status}`)
      }

      setCloseStatus(
        `${payload.closedPositions || 0} closed, ${payload.remainingPositions || 0} remaining. Realized ${formatCurrency(payload.realizedPnlUsd)}.`
      )
      await loadSummary()
    } catch (caught) {
      setCloseStatus(caught instanceof Error ? caught.message : 'Unable to close paper position.')
    } finally {
      setClosingId('')
    }
  }

  const summary = data?.summary || {}
  const openPositions = data?.openPositions || []
  const pendingEntries = data?.pendingEntries || []
  // Show the complete daily history, newest first. Sort explicitly so the view
  // is independent of the API's ordering.
  const dailyRows = [...(data?.daily || [])].sort((a, b) => (a.date < b.date ? 1 : -1))
  const rollingRows = data?.rollingWindows || []
  const recentTrades = data?.recentClosedTrades || []
  const isLoading = status === 'loading'

  return (
    <>
      <div className="utility-row">
        <div className="status-panel">
          <span className={`status-dot ${status}`}></span>
          <span>{status === 'ready' ? `Updated ${formatDateTime(data?.generatedAtUtc)}` : status.replace('-', ' ')}</span>
          <button type="button" onClick={loadSummary} disabled={isLoading}>
            {isLoading ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="alert">{error}</div>}

      <section className="paper-controls" aria-label="Paper trading controls">
        <input
          type="password"
          value={closeKey}
          onChange={(event) => saveCloseKey(event.target.value)}
          placeholder="Function key for closing an active coin"
          aria-label="Function key for closing paper positions"
          autoComplete="off"
        />
        {closeStatus && <span className={closeStatus.includes('closed') ? 'positive' : ''}>{closeStatus}</span>}
      </section>

      <section className="scoreboard" aria-label="Profit and loss summary">
        <Metric label="Total P/L" value={formatCurrency(summary.totalPnlUsd)} tone={pnlClass(summary.totalPnlUsd)} />
        <Metric label="Realized" value={formatCurrency(summary.realizedPnlUsd)} tone={pnlClass(summary.realizedPnlUsd)} />
        <Metric label="Unrealized" value={formatCurrency(summary.unrealizedPnlUsd)} tone={pnlClass(summary.unrealizedPnlUsd)} />
        <Metric label="Cash" value={formatCurrency(summary.availableCashUsd)} />
        <Metric label="Equity" value={formatCurrency(summary.totalEquityUsd)} tone={pnlClass(summary.totalPnlUsd)} />
        <Metric label="Total Invested" value={formatCurrency(summary.totalInvestedUsd)} />
        <Metric label="Allocated" value={formatCurrency(summary.allocatedUsd)} />
        <Metric label="Pending" value={summary.pendingEntries ?? pendingEntries.length} />
        <Metric label="Reserved" value={formatCurrency(summary.pendingReservedUsd)} />
        <Metric label="Open" value={summary.openPositions ?? 0} />
        <Metric label="Closed" value={summary.closedTrades ?? 0} />
        <Metric label="Win Rate" value={formatPercent(summary.winRatePct)} />
      </section>

      {pendingEntries.length > 0 && (
        <Panel title="Pending Limit Entries" meta={`${pendingEntries.length} waiting for retest`}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Coin</th>
                  <th>Limit</th>
                  <th>Signal</th>
                  <th>Support</th>
                  <th>Buy Zone</th>
                  <th>Reserved</th>
                  <th>Strategy</th>
                  <th>Expires</th>
                </tr>
              </thead>
              <tbody>
                {pendingEntries.map((entry) => (
                  <tr key={`${entry.productId}-${entry.createdAtUtc}`}>
                    <td className="strong">{entry.productId}</td>
                    <td>{formatPrice(entry.limitPriceUsd)}</td>
                    <td>{formatPrice(entry.signalPriceUsd)}</td>
                    <td>{formatPrice(entry.supportLevelUsd)}</td>
                    <td>{formatPrice(entry.buyZoneLowUsd)} - {formatPrice(entry.buyZoneHighUsd)}</td>
                    <td>{formatCurrency(entry.allocatedUsd)}</td>
                    <td>
                      <span className="badge neutral">{entry.strategy || 'Limit retest'}</span>
                      <small className="table-note">Score {formatNumber(entry.strategyScore, 0)}</small>
                    </td>
                    <td>{formatDateTime(entry.expiresAtUtc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <section className="grid-two">
        <Panel title="Open Positions" meta={`${openPositions.length} active`}>
          {openPositions.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Coin</th>
                    <th>Entry</th>
                    <th>Mark</th>
                    <th>P/L</th>
                    <th>Invested</th>
                    <th>Trail Stop</th>
                    <th>Take Profit</th>
                    <th>Held</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {openPositions.map((position) => {
                    const entryMs = position.entryTimestampUtc ? new Date(position.entryTimestampUtc).getTime() : 0
                    const heldH = entryMs ? Math.floor((Date.now() - entryMs) / 3600000) : null
                    const heldLabel = heldH !== null ? (heldH >= 24 ? `${Math.floor(heldH/24)}d ${heldH%24}h` : `${heldH}h`) : '—'
                    return (
                      <tr key={position.productId}>
                        <td className="strong">{position.productId}</td>
                        <td>{formatPrice(position.entryPriceUsd)}</td>
                        <td>{formatPrice(position.markPriceUsd)}</td>
                        <td className={pnlClass(position.unrealizedPnlUsd)}>
                          {formatCurrency(position.unrealizedPnlUsd)} <small>{formatPercent(position.unrealizedPnlPct)}</small>
                        </td>
                        <td>{formatCurrency(position.originalAllocatedUsd || position.allocatedUsd)}</td>
                        <td>{formatPrice(position.currentTrailingStop)}</td>
                        <td>{formatPrice(position.takeProfitBoundary)}</td>
                        <td>{heldLabel}</td>
                        <td>{position.partialTakeProfitTaken ? <span className="badge positive">🌙 Moon Bag</span> : <span className="badge neutral">Running</span>}</td>
                        <td>
                          <button
                            type="button"
                            className="table-action"
                            onClick={() => closePosition(position.productId)}
                            disabled={closingId === position.productId}
                          >
                            {closingId === position.productId ? 'Closing' : 'Close'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="No open positions" text="Bot is scanning every 5 min. It enters when score ≥ 70/100 + liquidity ($1M vol) + OBV pressure + BTC regime pass. Check Crypto Scalp tab for live scores." />
          )}
        </Panel>

        <Panel title="Daily P/L" meta={`${dailyRows.length} days`}>
          {dailyRows.length || rollingRows.length ? (
            <div className="daily-list">
              {rollingRows.map((window) => (
                <div className="daily-row" key={`rolling-${window.days}`}>
                  <span>{window.days}d rolling</span>
                  <span>{(window.closedTrades || 0)} closed / {(window.partialTakes || 0)} partial</span>
                  <strong className={pnlClass(window.realizedPnlUsd)}>{formatCurrency(window.realizedPnlUsd)}</strong>
                </div>
              ))}
              {dailyRows.map((day) => (
                <div className="daily-row" key={day.date}>
                  <span>{day.date}</span>
                  <span>{(day.closedTrades || 0)} closed / {(day.partialTakes || 0)} partial</span>
                  <strong className={pnlClass(day.realizedPnlUsd)}>{formatCurrency(day.realizedPnlUsd)}</strong>
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
                  const pnl = trade.pnlUsd
                  return (
                    <tr key={`${trade.productId}-${trade.exitTimestampUtc || index}`}>
                      <td className="strong">{trade.productId}</td>
                      <td>{formatDateTime(trade.exitTimestampUtc)}</td>
                      <td>{trade.exitReason || 'Closed'}</td>
                      <td>{formatPrice(trade.entryPriceUsd)}</td>
                      <td>{formatPrice(trade.exitPriceUsd)}</td>
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
            <strong>{formatPrice(analysis.price)}</strong>
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
    <section className="tool-page">
      <ResearchPlaybook />
      <RunReport
        apiKey={apiKey}
        functionUrl={functionUrl}
        onOpenSettings={onOpenSettings}
        endpoint="run"
        title="Research Summary"
        description="Runs the stock and crypto research pass and returns candidates, scores, buy ranges, stops, targets, rejected names, and options research ideas when a licensed options chain is configured."
        resultKey="candidates"
        emptyTitle="No candidates returned"
      />
    </section>
  )
}

function ResearchPlaybook() {
  return (
    <>
      <Panel title="Strategy Research Playbook" meta="Patterns and failure checks">
        <div className="research-grid">
          {strategyPlaybook.map((item) => (
            <article className="research-card" key={item.title}>
              <h3>{item.title}</h3>
              <p>{item.bestFor}</p>
              <strong>Confirm with</strong>
              <ul>
                {item.confirms.map((point) => <li key={point}>{point}</li>)}
              </ul>
              <strong>Avoid when</strong>
              <ul>
                {item.avoid.map((point) => <li key={point}>{point}</li>)}
              </ul>
            </article>
          ))}
        </div>
      </Panel>

      <section className="grid-two">
        <Panel title="Indicator Stack" meta="What each signal is doing">
          <div className="indicator-guide">
            {indicatorGuide.map((item) => (
              <article key={item.name}>
                <strong>{item.name}</strong>
                <span>{item.use}</span>
                <p>{item.good}</p>
                <small>{item.caution}</small>
              </article>
            ))}
          </div>
        </Panel>

        <Panel title="Lotto Options Framework" meta="High risk research only">
          <div className="lotto-panel">
            <p>
              Use options ideas as a shortlist for defined-risk research. These are not
              guaranteed buys; the premium can go to zero, especially on short-dated contracts.
            </p>
            <ul>
              {lottoOptionsRules.map((rule) => <li key={rule}>{rule}</li>)}
            </ul>
          </div>
        </Panel>
      </section>
    </>
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
            const posPnlClass = pos.unrealized_pnl_pct > 0 ? 'positive' : pos.unrealized_pnl_pct < 0 ? 'negative' : ''
            return (
              <tr key={`${pos.symbol}-${i}`}>
                <td className="strong">{pos.symbol}</td>
                <td>{formatPrice(pos.entry_price)}</td>
                <td>{pos.current_price > 0 ? formatPrice(pos.current_price) : '—'}</td>
                <td className={posPnlClass}>
                  {pos.current_price > 0
                    ? `${pos.unrealized_pnl_pct > 0 ? '+' : ''}${pos.unrealized_pnl_pct.toFixed(2)}% ($${pos.unrealized_pnl_usd > 0 ? '+' : ''}${pos.unrealized_pnl_usd.toFixed(2)})`
                    : '—'}
                </td>
                <td>${(pos.allocated_usd || 0).toFixed(2)}</td>
                <td>{pos.trail_pct.toFixed(1)}%</td>
                <td>{pos.current_stop > 0 ? formatPrice(pos.current_stop) : '—'}</td>
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
                <td>{formatPrice(row.price)}</td>
                <td>{formatPrice(row.stop_loss)}</td>
                <td>{formatPrice(row.target1)} / {formatPrice(row.target2)}</td>
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
            {mode === 'candidates' && <th>Options Research</th>}
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
              {mode === 'movers' && <td>{formatPrice(row.price)}</td>}
              {mode === 'movers' && <td className={pnlClass(row.changePct)}>{formatPercent(row.changePct)}</td>}
              {mode !== 'movers' && <td>{formatPrice(row.buyRangeLow)} – {formatPrice(row.buyRangeHigh)}</td>}
              {mode !== 'movers' && <td>{formatPrice(row.stopLoss)}</td>}
              {mode !== 'movers' && <td>{formatPrice(row.target1)} / {formatPrice(row.target2)}</td>}
              {mode === 'candidates' && <td>{row.option ? <span className="option-idea">{row.option}</span> : <span className="muted-text">No liquid contract</span>}</td>}
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
            <span>Support {formatPrice(item.levels?.support)}</span>
            <span>Resistance {formatPrice(item.levels?.resistance)}</span>
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
          <Metric label="Entry" value={formatPrice(rec.levels.entry)} />
          <Metric label="Stop" value={formatPrice(rec.levels.stopLoss)} tone="negative" />
          <Metric label="Target 1" value={formatPrice(rec.levels.target1)} tone="positive" />
          <Metric label="Target 2" value={formatPrice(rec.levels.target2)} tone="positive" />
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

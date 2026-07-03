import { useState } from 'react';

function StyleCard({ title, rec }) {
  if (!rec || !rec.available) return null;

  const getBiasColor = (bias) => {
    if (bias === 'Bullish') return 'text-green-600 bg-green-50';
    if (bias === 'Bearish') return 'text-red-600 bg-red-50';
    return 'text-gray-600 bg-gray-50';
  };

  return (
    <div className="bg-white rounded-lg shadow p-6 border-l-4 border-blue-500">
      <h3 className="text-lg font-bold mb-2">{title}</h3>
      <div className={`inline-block px-3 py-1 rounded text-sm font-bold mb-4 ${getBiasColor(rec.bias)}`}>
        {rec.bias} {rec.strength && `(${rec.strength})`}
      </div>

      {rec.levels && (
        <div className="grid grid-cols-2 gap-2 text-sm mb-4 font-mono">
          <div>
            <span className="text-gray-600">Entry:</span> <span className="font-bold">${rec.levels.entry?.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-600">Stop:</span> <span className="font-bold text-red-600">${rec.levels.stopLoss?.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-600">T1:</span> <span className="font-bold text-green-600">${rec.levels.target1?.toFixed(2)}</span>
          </div>
          {rec.levels.target2 && (
            <div>
              <span className="text-gray-600">T2:</span> <span className="font-bold text-green-600">${rec.levels.target2?.toFixed(2)}</span>
            </div>
          )}
          {rec.levels.riskPct && (
            <div>
              <span className="text-gray-600">Risk:</span> <span>{rec.levels.riskPct?.toFixed(1)}%</span>
            </div>
          )}
          {rec.levels.rewardRisk && (
            <div>
              <span className="text-gray-600">R:R:</span> <span className="font-bold">{rec.levels.rewardRisk?.toFixed(1)}:1</span>
            </div>
          )}
        </div>
      )}

      {rec.signals && rec.signals.length > 0 && (
        <div className="text-sm">
          <p className="font-semibold text-gray-700 mb-2">Signals:</p>
          <ul className="list-disc list-inside space-y-1 text-gray-600">
            {rec.signals.slice(0, 5).map((signal, index) => (
              <li key={index}>{signal}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function TickerAnalyzer({ apiKey, functionUrl }) {
  const [symbol, setSymbol] = useState('');
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!symbol.trim()) return;

    setLoading(true);
    setError(null);
    setAnalysis(null);

    try {
      const url = `${functionUrl}/api/analyze/${symbol.toUpperCase()}?code=${apiKey}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setAnalysis(data);
    } catch (err) {
      setError(err.message || 'Failed to fetch analysis');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <form onSubmit={handleSearch} className="bg-white rounded-lg shadow p-6">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Enter ticker (e.g., BTC-USD, NVDA, ETH-USD)"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="flex-1 px-4 py-2 border rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={loading || !symbol}
            className="px-6 py-2 bg-blue-600 text-white rounded font-semibold hover:bg-blue-700 disabled:bg-gray-400 transition"
          >
            {loading ? 'Analyzing...' : 'Analyze'}
          </button>
        </div>
      </form>

      {error && (
        <div className="bg-red-50 border border-red-300 text-red-700 px-4 py-3 rounded">
          Error: {error}
        </div>
      )}

      {analysis && (
        <div className="space-y-4">
          <div className="bg-gradient-to-r from-blue-600 to-purple-600 text-white rounded-lg shadow p-6">
            <div className="flex justify-between items-center">
              <div>
                <h2 className="text-3xl font-bold">{analysis.symbol}</h2>
                <p className="text-blue-100">{analysis.assetClass}</p>
              </div>
              <div className="text-right">
                <p className="text-4xl font-bold">${analysis.price?.toFixed(2)}</p>
                <p className="text-blue-100 text-sm">Current Price</p>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StyleCard title="☄️ Scalp (15m/4H)" rec={analysis.recommendations?.scalp} />
            <StyleCard title="📈 Swing (4H/Daily)" rec={analysis.recommendations?.swing} />
            <StyleCard title="🚀 Long-Term (Daily)" rec={analysis.recommendations?.longTerm} />
          </div>
        </div>
      )}
    </div>
  );
}

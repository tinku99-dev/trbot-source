namespace TradingResearchBot.Models;

/// <summary>Computed technical indicator snapshot for the most recent bar.</summary>
public sealed record IndicatorSet
{
    public decimal Price { get; init; }

    public decimal? Sma20 { get; init; }
    public decimal? Sma50 { get; init; }
    public decimal? Sma200 { get; init; }
    public decimal? Ema12 { get; init; }
    public decimal? Ema26 { get; init; }

    public decimal? Rsi14 { get; init; }

    public decimal? Macd { get; init; }
    public decimal? MacdSignal { get; init; }
    public decimal? MacdHistogram { get; init; }

    public decimal? BollingerUpper { get; init; }
    public decimal? BollingerMiddle { get; init; }
    public decimal? BollingerLower { get; init; }

    public decimal? Atr14 { get; init; }

    public decimal? Support { get; init; }
    public decimal? Resistance { get; init; }

    /// <summary>Latest volume divided by trailing 30-bar average volume.</summary>
    public decimal? VolumeRelativeStrength { get; init; }

    /// <summary>Percent distance of price above/below the 200 SMA.</summary>
    public decimal? PctFromSma200 { get; init; }

    // --- Advanced momentum / trend / volume indicators ---

    /// <summary>Stochastic oscillator %K (14).</summary>
    public decimal? StochasticK { get; init; }

    /// <summary>Stochastic oscillator %D (3-period SMA of %K).</summary>
    public decimal? StochasticD { get; init; }

    /// <summary>Average Directional Index (14) — trend strength (0-100).</summary>
    public decimal? Adx14 { get; init; }

    /// <summary>On-Balance Volume — cumulative volume flow.</summary>
    public decimal? Obv { get; init; }

    /// <summary>OBV slope over the trailing window (positive = accumulation).</summary>
    public decimal? ObvSlope { get; init; }

    /// <summary>OBV change as a percent of total volume over the trailing window.</summary>
    public decimal? ObvPressurePct { get; init; }

    /// <summary>Share of trailing volume that traded on up-closing bars.</summary>
    public decimal? ObvUpVolumeRatio { get; init; }

    /// <summary>Rolling VWAP (volume-weighted average price) over the lookback.</summary>
    public decimal? Vwap { get; init; }

    /// <summary>Money Flow Index (14) — volume-weighted RSI (0-100).</summary>
    public decimal? Mfi14 { get; init; }

    /// <summary>Williams %R (14) — momentum (-100..0).</summary>
    public decimal? WilliamsR { get; init; }

    /// <summary>Commodity Channel Index (20).</summary>
    public decimal? Cci20 { get; init; }

    /// <summary>20-day annualized historical volatility (fraction, e.g. 0.45 = 45%).</summary>
    public decimal? HistoricalVolatility { get; init; }

    /// <summary>Composite conviction score 0-100 summarizing buy-side strength.</summary>
    public decimal? ConvictionScore { get; init; }
}

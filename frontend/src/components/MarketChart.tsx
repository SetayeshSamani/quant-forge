import { useState } from "react";
import { demoCandles, TIMEFRAMES, type Timeframe } from "../data/demo";
export function MarketChart() {
  const [timeframe, setTimeframe] = useState<Timeframe>("15m");
  const candles = demoCandles(timeframe);
  const priceY = (price: number) => 230 - (price - 62750) / 11;
  const series = (offset: number) =>
    candles
      .map(
        (_, i) =>
          `${14 + i * 9.15},${priceY(62840 + i * 14.5 - offset + Math.sin(i / 14) * 40)}`,
      )
      .join(" ");
  return (
    <section className="panel chart-panel" aria-label="نمودار نمایشی BTC/USDT">
      <div className="chart-toolbar" dir="ltr">
        <div className="market-identity">
          <span className="bitcoin" aria-hidden="true">
            ₿
          </span>
          <strong>BTC/USDT</strong>
          <b className="quote-price">64,250.00</b>
          <span className="quote-change">+1.16% (+326.40)</span>
        </div>
        <div
          className="chart-tabs"
          role="group"
          aria-label="بازه نمودار نمایشی"
        >
          {TIMEFRAMES.map((item) => (
            <button
              key={item}
              aria-pressed={item === timeframe}
              onClick={() => setTimeframe(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-legend" dir="ltr">
        <span>
          <i className="line-blue" />
          EMA20 <b>63,980.41</b>
        </span>
        <span>
          <i className="line-gold" />
          EMA50 <b>63,620.17</b>
        </span>
        <small>مقادیر و نمودار نمونه</small>
      </div>
      <div className="chart-drawing" dir="ltr">
        <svg
          viewBox="0 0 900 310"
          preserveAspectRatio="none"
          role="img"
          aria-labelledby="chart-title chart-description"
        >
          <title id="chart-title">
            نمودار مصنوعی بیت‌کوین، بازه {timeframe}
          </title>
          <desc id="chart-description">
            کندل، حجم و خطوط میانگین صرفاً تصویرسازی DEMO هستند؛ داده بازار یا
            سیگنال معامله نیستند.
          </desc>
          {[65000, 64500, 64000, 63500, 63000, 62800].map((price) => (
            <g key={price}>
              <line
                className="chart-grid"
                x1="8"
                x2="826"
                y1={priceY(price)}
                y2={priceY(price)}
              />
              <text className="chart-label" x="838" y={priceY(price) + 4}>
                {price.toLocaleString("en-US")}
              </text>
            </g>
          ))}
          {Array.from({ length: 9 }, (_, i) => (
            <g key={i}>
              <line
                className="chart-grid"
                x1={20 + i * 98}
                x2={20 + i * 98}
                y1="5"
                y2="283"
              />
              <text
                className="chart-label"
                textAnchor="middle"
                x={22 + i * 98}
                y="304"
              >
                {timeframe === "15m"
                  ? `${String(i + 8).padStart(2, "0")}:00`
                  : `${i + 1} ${timeframe === "1D" ? "روز" : "بازه"}`}
              </text>
            </g>
          ))}
          <line className="chart-grid" x1="8" x2="826" y1="245" y2="245" />
          {candles.map((candle, i) => {
            const x = 14 + i * 9.15;
            const color =
              candle.close >= candle.open
                ? "var(--positive)"
                : "var(--negative)";
            return (
              <g key={i}>
                <line
                  x1={x}
                  x2={x}
                  y1={priceY(candle.high)}
                  y2={priceY(candle.low)}
                  stroke={color}
                />
                <rect
                  x={x - 2.6}
                  y={Math.min(priceY(candle.open), priceY(candle.close))}
                  width="5.2"
                  height={Math.max(
                    2,
                    Math.abs(priceY(candle.open) - priceY(candle.close)),
                  )}
                  fill={color}
                />
                <rect
                  x={x - 2.6}
                  y={283 - candle.volume * 0.28}
                  width="5.2"
                  height={candle.volume * 0.28}
                  fill={color}
                  opacity=".58"
                />
              </g>
            );
          })}
          <polyline
            points={series(0)}
            fill="none"
            stroke="var(--blue)"
            strokeWidth="1.7"
          />
          <polyline
            points={series(145)}
            fill="none"
            stroke="var(--gold)"
            strokeWidth="1.7"
          />
          <rect
            x="832"
            y="66"
            width="66"
            height="19"
            rx="3"
            fill="var(--positive)"
          />
          <text x="865" y="80" textAnchor="middle" className="price-tag">
            64,250.00
          </text>
          <text className="chart-label" x="838" y="262">
            2.5K
          </text>
          <text className="chart-label" x="838" y="282">
            0
          </text>
        </svg>
        <div className="chart-watermark">
          <strong>DEMO DATA</strong>
          <span>داده‌های نمایشی است</span>
        </div>
      </div>
    </section>
  );
}

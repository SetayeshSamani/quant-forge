import { Icon, type IconName } from "./Icon";
export function MetricCard({
  label,
  value,
  icon,
  tone = "blue",
  graphic,
}: {
  label: string;
  value: string;
  icon: IconName;
  tone?: "blue" | "green" | "gold";
  graphic?: "spark" | "bars" | "progress";
}) {
  return (
    <article
      className={`metric-card tone-${tone}`}
      aria-label={`${label}، داده نمایشی`}
    >
      <div className="metric-copy">
        <span className="metric-label">{label}</span>
        <strong>{value}</strong>
        <span className="metric-unit" dir="ltr">
          USDT {tone === "green" && <b>+1.16%</b>}
        </span>
      </div>
      <span className="metric-icon">
        <Icon name={icon} />
      </span>
      {graphic === "spark" && (
        <svg
          className="metric-spark"
          viewBox="0 0 150 70"
          fill="none"
          aria-hidden="true"
        >
          <defs>
            <linearGradient id={`spark-${tone}`} x2="0" y2="1">
              <stop stopColor="currentColor" stopOpacity=".2" />
              <stop offset="1" stopColor="currentColor" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            d="m1 65 8-7 5 3 6-9 6 3 8-10 5 4 9-6 6 1 6-9 7 4 7-8 7 2 7-8 7 3 9-9 8 3 8-12 6 5 8-5 7-10"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path
            d="m1 65 8-7 5 3 6-9 6 3 8-10 5 4 9-6 6 1 6-9 7 4 7-8 7 2 7-8 7 3 9-9 8 3 8-12 6 5 8-5 7-10V70H1Z"
            fill={`url(#spark-${tone})`}
          />
        </svg>
      )}
      {graphic === "bars" && (
        <div className="metric-bars" aria-hidden="true">
          {[10, 22, 30, 23, 41].map((height, i) => (
            <i key={i} style={{ height }} />
          ))}
        </div>
      )}
      {graphic === "progress" && (
        <div className="metric-progress">
          <span className="progress-track">
            <i style={{ width: "34%" }} />
          </span>
          <small>از ۱۴٬۰۰۰ USDT</small>
        </div>
      )}
    </article>
  );
}

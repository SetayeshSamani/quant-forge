import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { Badge } from "./Panel";
import { DEMO } from "../data/demo";
export type Theme = "dark" | "light";
export const PAGE_TITLES = {
  overview: "نمای کلی",
  "credit-risk": "اعتبار و ریسک",
  "decision-center": "مرکز تصمیم",
  "strategy-market": "استراتژی و بازار",
  "positions-orders": "موقعیت‌ها و سفارش‌ها",
  "backtest-analytics": "بک‌تست و تحلیل",
  "robot-control": "کنترل ربات",
  "alerts-events": "هشدارها و رویدادها",
  connections: "اتصال و تنظیمات",
} as const;
export type PageId = keyof typeof PAGE_TITLES;
const navigation: readonly { label: string; icon: IconName; page?: PageId }[] =
  [
    { label: "نمای کلی", icon: "home", page: "overview" },
    { label: "اعتبار و ریسک", icon: "shield", page: "credit-risk" },
    { label: "استراتژی و بازار", icon: "chart", page: "strategy-market" },
    { label: "مرکز تصمیم", icon: "nodes", page: "decision-center" },
    { label: "موقعیت‌ها و سفارش‌ها", icon: "orders", page: "positions-orders" },
    { label: "بک‌تست و تحلیل", icon: "pie", page: "backtest-analytics" },
    { label: "کنترل ربات", icon: "bot", page: "robot-control" },
    { label: "هشدارها و رویدادها", icon: "bell", page: "alerts-events" },
    { label: "اتصال و تنظیمات", icon: "settings", page: "connections" },
  ];
export function AppShell({
  children,
  page,
  theme,
  setTheme,
  sidebarOpen,
  setSidebarOpen,
  onNotifications,
  demoView = true,
}: {
  demoView?: boolean;
  children: ReactNode;
  page: PageId;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  onNotifications: () => void;
}) {
  const demoPage = demoView && (page === "overview" || page === "credit-risk" || page === "decision-center");
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        رفتن به محتوای اصلی
      </a>
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          aria-label="بستن فهرست"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside
        className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}
        id="main-sidebar"
        aria-label="ناوبری اصلی"
      >
        <a className="brand" href="#overview" aria-label="QuantForge، نمای کلی">
          <div dir="ltr">
            <img src="/brand.svg" alt="" width="48" height="52" />
            <strong>QuantForge</strong>
          </div>
          <span>پلتفرم معاملات الگوریتمی</span>
        </a>
        <nav aria-label="بخش‌های برنامه">
          <ul>
            {navigation.map(({ label, icon, page: destination }) => (
              <li key={label}>
                {destination ? (
                  <a
                    href={`#${destination}`}
                    className={`nav-item ${page === destination ? "active" : ""}`}
                    aria-current={page === destination ? "page" : undefined}
                    onClick={() => setSidebarOpen(false)}
                  >
                    <Icon name={icon} size={25} />
                    <span>{label}</span>
                  </a>
                ) : (
                  <button
                    className="nav-item"
                    disabled
                    title="در مرحله بعد پیاده‌سازی می‌شود"
                    aria-label={`${label}؛ به‌زودی`}
                  >
                    <Icon name={icon} size={25} />
                    <span>{label}</span>
                    <small className="nav-soon">به‌زودی</small>
                  </button>
                )}
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-bottom">
          <div className="demo-account">
            <Icon name="user" size={30} />
            <div>
              <span>{demoPage ? "حساب نمایشی" : "اتصال حساب به UI"}</span>
              <b dir="ltr">{demoPage ? DEMO.account : "UNVERIFIED"}</b>
            </div>
          </div>
          <button className="trade-disabled" disabled>
            <Icon name="lock" size={15} />
            <span dir="ltr">NO_TRADE</span> · اجرای معامله غیرفعال
          </button>
          <small>پلتفرم مستقل</small>
        </div>
      </aside>
      <div className="workspace" id={page}>
        <header className="topbar">
          <div className="page-heading">
            <button
              className="icon-button mobile-menu"
              aria-label="باز کردن فهرست"
              aria-expanded={sidebarOpen}
              aria-controls="main-sidebar"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Icon name="menu" />
            </button>
            <div>
              <h1>{PAGE_TITLES[page]}</h1>
              <p>{demoPage ? "حساب اعتباری والکس · سناریوی نمایشی" : demoView ? "گزارش محلی فقط‌خواندنی · بدون اتصال زنده" : "API عمومی فقط‌خواندنی · بدون مجوز معامله"}</p>
            </div>
          </div>
          <div className="header-controls">
            <span className="read-only">
              <Icon name="book" />
              فقط خواندنی
            </span>
            <Badge tone="danger">
              <Icon name="pause" size={17} />
              {demoPage ? "متوقف" : "اجرای معامله غیرفعال"}
            </Badge>
            <Badge tone="info">{demoPage ? "DEMO" : "READ ONLY"}</Badge>
            <div
              className="theme-switch"
              role="group"
              aria-label="انتخاب پوسته"
            >
              <button
                aria-label="پوسته روشن"
                aria-pressed={theme === "light"}
                onClick={() => setTheme("light")}
              >
                <Icon name="sun" size={19} />
                <span>روشن</span>
              </button>
              <button
                aria-label="پوسته تیره"
                aria-pressed={theme === "dark"}
                onClick={() => setTheme("dark")}
              >
                <Icon name="moon" size={19} />
                <span>تیره</span>
              </button>
            </div>
            <button
              className="icon-button notification-button"
              aria-label={demoView ? "هشدارهای نمایشی" : "هشدارها و رویدادها"}
              onClick={demoView ? onNotifications : () => {window.location.hash="alerts-events";}}
            >
              <Icon name="bell" size={26} />
              <i />
            </button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}

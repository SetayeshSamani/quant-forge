import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { Badge } from "./Panel";
import { DEMO } from "../data/demo";
export type Theme = "dark" | "light";
const navigation: readonly { label: string; icon: IconName }[] = [
  { label: "نمای کلی", icon: "home" },
  { label: "اعتبار و ریسک", icon: "shield" },
  { label: "استراتژی و بازار", icon: "chart" },
  { label: "مرکز تصمیم", icon: "nodes" },
  { label: "موقعیت‌ها و سفارش‌ها", icon: "orders" },
  { label: "بک‌تست و تحلیل", icon: "pie" },
  { label: "کنترل ربات", icon: "bot" },
  { label: "هشدارها و رویدادها", icon: "bell" },
  { label: "اتصال و تنظیمات", icon: "settings" },
];
export function AppShell({
  children,
  theme,
  setTheme,
  sidebarOpen,
  setSidebarOpen,
  onNotifications,
}: {
  children: ReactNode;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  onNotifications: () => void;
}) {
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
            {navigation.map(({ label, icon }, index) => (
              <li key={label}>
                {index === 0 ? (
                  <a
                    href="#overview"
                    className="nav-item active"
                    aria-current="page"
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
              <span>حساب نمایشی</span>
              <b dir="ltr">{DEMO.account}</b>
            </div>
          </div>
          <button className="trade-disabled" disabled>
            <Icon name="lock" size={15} />
            <span dir="ltr">NO_TRADE</span> · اجرای معامله غیرفعال
          </button>
          <small>پلتفرم مستقل</small>
        </div>
      </aside>
      <div className="workspace" id="overview">
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
              <h1>نمای کلی</h1>
              <p>حساب اعتباری والکس · سناریوی نمایشی</p>
            </div>
          </div>
          <div className="header-controls">
            <span className="read-only">
              <Icon name="book" />
              فقط خواندنی
            </span>
            <Badge tone="danger">
              <Icon name="pause" size={17} />
              متوقف
            </Badge>
            <Badge tone="info">DEMO</Badge>
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
              aria-label="هشدارهای نمایشی"
              onClick={onNotifications}
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

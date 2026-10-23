import { useEffect, useRef, useState } from "react";
import { AppShell, type Theme, type PageId } from "./components/AppShell";
import { Icon } from "./components/Icon";
import { Overview } from "./pages/Overview";
import { DecisionCenter } from "./pages/DecisionCenter";

const pageFromHash = (): PageId =>
  window.location.hash === "#decision-center" ? "decision-center" : "overview";
export function App() {
  const [page, setPage] = useState<PageId>(pageFromHash);
  useEffect(() => {
    const navigate = () => setPage(pageFromHash());
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);
  useEffect(() => {
    document.title = `${page === "decision-center" ? "مرکز تصمیم" : "نمای کلی"} | QuantForge DEMO`;
  }, [page]);
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notice, setNotice] = useState<"risk" | "notifications" | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const showNotice = (value: "risk" | "notifications") => {
    trigger.current = document.activeElement as HTMLElement;
    setNotice(value);
  };
  const closeNotice = () => {
    dialog.current?.close();
    setNotice(null);
    trigger.current?.focus();
  };
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "dark" ? "#04172a" : "#f7f9fc");
    try {
      localStorage.setItem("quantforge.theme", theme);
    } catch {
      /* Optional persistence. */
    }
  }, [theme]);
  useEffect(() => {
    if (notice && !dialog.current?.open) dialog.current?.showModal();
  }, [notice]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [sidebarOpen]);
  return (
    <>
      <AppShell
        page={page}
        theme={theme}
        setTheme={setTheme}
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        onNotifications={() => showNotice("notifications")}
      >
        {page === "decision-center" ? (
          <DecisionCenter />
        ) : (
          <Overview onReviewRisk={() => showNotice("risk")} />
        )}
      </AppShell>
      <dialog
        ref={dialog}
        className="notice-dialog"
        aria-labelledby="notice-title"
        onCancel={(event) => {
          event.preventDefault();
          closeNotice();
        }}
      >
        <div className="dialog-heading">
          <h2 id="notice-title">
            {notice === "risk" ? "اعتبار و ریسک · DEMO" : "هشدارهای نمایشی"}
          </h2>
          <button
            className="icon-button"
            aria-label="بستن پنجره"
            onClick={closeNotice}
          >
            <Icon name="close" />
          </button>
        </div>
        <p>
          این صفحه از داده‌های مصنوعی استفاده می‌کند. هیچ حساب یا API به برنامه
          متصل نیست.
        </p>
        <ul>
          <li>وضعیت پلن، تازگی داده و آستانه لیکویید تأیید نشده است.</li>
          <li>تأیید ریسک و مجوز اجرای سفارش وجود ندارد.</li>
          <li>ورود جدید و تمام اقدامات معاملاتی غیرفعال هستند.</li>
        </ul>
        <div className="dialog-safety" dir="ltr">
          NO_TRADE · executable_ready = false
        </div>
        <button className="primary-button" onClick={closeNotice}>
          متوجه شدم
        </button>
      </dialog>
    </>
  );
}

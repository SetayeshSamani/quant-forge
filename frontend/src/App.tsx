import { useEffect, useRef, useState } from "react";
import {
  AppShell,
  PAGE_TITLES,
  type Theme,
  type PageId,
} from "./components/AppShell";
import { Icon } from "./components/Icon";
import { Overview } from "./pages/Overview";
import { DecisionCenter } from "./pages/DecisionCenter";
import { CreditRisk } from "./pages/CreditRisk";
import { Operations } from "./pages/Operations";
import { ConnectionIndicator, BackendView } from "./pages/BackendView";
import { useObserver } from "./data/useObserver";
import type { EvidenceState, ImportedEvidence } from "./data/runtimeEvidence";

const pageFromHash = (): PageId => {
  const hash = window.location.hash.slice(1);
  return Object.hasOwn(PAGE_TITLES, hash) ? hash as PageId : "overview";
};
export function App() {
  const demoView = new URLSearchParams(window.location.search).get("view") === "demo";
  const observer = useObserver(!demoView);
  const [page, setPage] = useState<PageId>(pageFromHash);
  const [evidence, setEvidence] = useState<EvidenceState>({ public: null, account: null });
  const importEvidence = (value: ImportedEvidence) => setEvidence(current => ({ ...current, [value.kind]: value }));
  useEffect(() => {
    const navigate = () => setPage(pageFromHash());
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);
  useEffect(() => {
    const demo = demoView && (page === "overview" || page === "credit-risk" || page === "decision-center");
    document.title = `${PAGE_TITLES[page]} | QuantForge ${demo ? "DEMO" : "READ ONLY"}`;
  }, [page, demoView]);
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
        demoView={demoView}
        connectionStatus={!demoView ? <ConnectionIndicator connection={observer}/> : undefined}
        theme={theme}
        setTheme={setTheme}
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        onNotifications={() => showNotice("notifications")}
      >
        {demoView && <a className="subtle-button" href="/#overview">بازگشت به داشبورد متصل به API</a>}
        {!demoView ? (
          <BackendView page={page} connection={observer}/>
        ) : page === "credit-risk" ? (
          <CreditRisk />
        ) : page === "decision-center" ? (
          <DecisionCenter />
        ) : page === "overview" ? (
          <Overview onReviewRisk={() => showNotice("risk")} />
        ) : (
          <Operations page={page} evidence={evidence} onImport={importEvidence} onClear={() => setEvidence({ public: null, account: null })} />
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

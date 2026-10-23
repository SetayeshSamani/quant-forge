import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
export function Panel({
  title,
  icon,
  action,
  children,
  className = "",
}: {
  title: string;
  icon: IconName;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <div className="panel-heading">
        <h2>
          <Icon name={icon} />
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

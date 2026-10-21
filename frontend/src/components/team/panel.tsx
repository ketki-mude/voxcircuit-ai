import { type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

/** A flat card with a hairline border: an optional title row, content, and footer. */
export function Panel({
  title,
  action,
  footer,
  className,
  children,
}: {
  title?: ReactNode;
  /** Shown at the end of the title row, such as a chip or a button. */
  action?: ReactNode;
  footer?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={title ? titleId : undefined}
      className={cn("flex flex-col rounded-lg border-[0.5px] border-line bg-card", className)}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4">
          {title && (
            <h2 id={titleId} className="text-sm font-semibold">
              {title}
            </h2>
          )}
          {action}
        </header>
      )}
      <div className="flex flex-1 flex-col gap-3 p-4">{children}</div>
      {footer && (
        <footer className="flex flex-col gap-1 border-t-[0.5px] border-line px-4 py-3 text-xs text-muted-foreground">
          {footer}
        </footer>
      )}
    </section>
  );
}

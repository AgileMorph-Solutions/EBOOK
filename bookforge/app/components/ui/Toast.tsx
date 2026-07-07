"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { cn } from "@/lib/cn";

type ToastVariant = "error" | "success" | "warning" | "info";

type Toast = {
  id: string;
  variant: ToastVariant;
  title: string;
  message?: string;
  duration?: number;
};

type ToastContextValue = {
  addToast: (toast: Omit<Toast, "id">) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

const VARIANTS: Record<ToastVariant, { bar: string; icon: string; container: string }> = {
  error: {
    bar: "bg-error",
    icon: "error",
    container: "border-error/30 bg-surface-container text-on-surface",
  },
  success: {
    bar: "bg-tertiary",
    icon: "check_circle",
    container: "border-tertiary/30 bg-surface-container text-on-surface",
  },
  warning: {
    bar: "bg-primary",
    icon: "warning",
    container: "border-primary/30 bg-surface-container text-on-surface",
  },
  info: {
    bar: "bg-secondary",
    icon: "info",
    container: "border-outline/20 bg-surface-container text-on-surface",
  },
};

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: (id: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const v = VARIANTS[toast.variant];
  const duration = toast.duration ?? 6000;

  useEffect(() => {
    // Trigger enter animation on next frame
    const raf = requestAnimationFrame(() => setVisible(true));
    const timer = setTimeout(() => {
      setVisible(false);
      setTimeout(() => onDismiss(toast.id), 300);
    }, duration);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
    };
  }, [toast.id, duration, onDismiss]);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(
        "relative w-80 overflow-hidden rounded-lg border shadow-lg transition-all duration-300",
        v.container,
        visible ? "translate-x-0 opacity-100" : "translate-x-full opacity-0"
      )}
    >
      {/* colour bar */}
      <div className={cn("absolute inset-y-0 left-0 w-1", v.bar)} />

      <div className="flex items-start gap-3 px-4 py-3 pl-5">
        <span className={cn("material-symbols-outlined mt-0.5 text-lg leading-none", `text-${toast.variant === "error" ? "error" : toast.variant === "success" ? "tertiary" : "primary"}`)}>
          {v.icon}
        </span>

        <div className="min-w-0 flex-1">
          <p className="font-label text-label-sm font-medium">{toast.title}</p>
          {toast.message && (
            <p className="mt-0.5 font-body text-body-sm text-secondary line-clamp-3">
              {toast.message}
            </p>
          )}
        </div>

        <button
          type="button"
          onClick={() => {
            setVisible(false);
            setTimeout(() => onDismiss(toast.id), 300);
          }}
          className="ml-1 shrink-0 text-secondary hover:text-on-surface"
          aria-label="Dismiss"
        >
          <span className="material-symbols-outlined text-base leading-none">close</span>
        </button>
      </div>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const addToast = useCallback((toast: Omit<Toast, "id">) => {
    const id = `toast-${++counter.current}`;
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {/* Portal — fixed top-right stack */}
      <div
        aria-label="Notifications"
        className="pointer-events-none fixed right-4 top-4 z-50 flex flex-col items-end gap-2"
      >
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto">
            <ToastItem toast={t} onDismiss={dismiss} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

import { useEffect } from "react";
import { Link } from "react-router";

interface RowModalProps {
  open: boolean;
  onClose: () => void;
  onAccept?: () => void;
  onReject?: () => void;
  emailSentPending?: boolean;
  children: React.ReactNode;
}

export function RowModal({ open, onClose, onAccept, onReject, emailSentPending, children }: RowModalProps) {
  useEffect(() => {
    if (!open) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-40"
      onClick={onClose}
    >
      <div
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-surface rounded-xl shadow-2xl w-[calc(100%-2rem)] max-w-4xl max-h-[90vh] overflow-y-auto relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close dialog"
          className="absolute top-3 right-3 inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface-muted text-text-muted shadow-sm transition-colors duration-150 cursor-pointer hover:bg-border hover:text-text active:bg-border/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
        >
          <svg
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-5 w-5"
          >
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
        {children}

        {emailSentPending && (
          <div className="mx-6 mb-6 mt-2 rounded-lg border border-warning/40 bg-warning/10 px-5 py-4 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="text-lg leading-none">✉</span>
              <div>
                <p className="text-sm font-semibold text-text">Email Sent — Pending from the User</p>
                <p className="text-xs text-text-muted mt-0.5">
                  An approval email was sent to the business. Review and action in Email Log.
                </p>
              </div>
            </div>
            <Link
              to="/email-log"
              onClick={onClose}
              className="shrink-0 text-xs font-semibold text-primary hover:underline"
            >
              Go to Email Log →
            </Link>
          </div>
        )}

        {(onAccept || onReject) && (
          <div className="flex gap-3 px-6 pb-6 pt-2">
            {onAccept && (
              <button
                onClick={onAccept}
                className="flex-1 bg-primary text-text-inverse rounded-lg py-3 text-sm font-semibold hover:bg-primary-hover"
              >
                Accept change
              </button>
            )}
            {onReject && (
              <button
                onClick={onReject}
                className="flex-1 border border-border text-text rounded-lg py-3 text-sm font-semibold hover:bg-surface-muted"
              >
                Reject change
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

import { useEffect } from "react";

interface RowModalProps {
  open: boolean;
  onClose: () => void;
  onAccept?: () => void;
  onReject?: () => void;
  children: React.ReactNode;
}

export function RowModal({ open, onClose, onAccept, onReject, children }: RowModalProps) {
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
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-surface rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-text-muted hover:text-text text-xl leading-none"
        >
          ×
        </button>
        {children}

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

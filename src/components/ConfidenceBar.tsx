export function ConfidenceBar({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-text-muted text-xs">—</span>;
  }

  const fillColor =
    value >= 0.85 ? "bg-success" : value >= 0.5 ? "bg-warning" : "bg-error";

  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1 rounded-full bg-confidence-bar-bg">
        <div
          className={`h-full rounded-full ${fillColor}`}
          style={{ width: `${value * 100}%` }}
        />
      </div>
      <span className="text-xs text-text-muted tabular-nums">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

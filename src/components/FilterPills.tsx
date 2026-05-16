type FilterOption = "All" | "Auto-Update" | "Needs Review" | "Email Sent" | "Website Offline";

interface FilterPillsProps {
  active: FilterOption;
  onSelect: (option: FilterOption) => void;
}

const OPTIONS: FilterOption[] = [
  "All",
  "Auto-Update",
  "Needs Review",
  "Email Sent",
  "Website Offline",
];

export function FilterPills({ active, onSelect }: FilterPillsProps) {
  return (
    <div className="flex gap-2">
      {OPTIONS.map((option) =>
        option === active ? (
          <button
            key={option}
            onClick={() => onSelect(option)}
            className="bg-primary text-text-inverse rounded-full px-4 py-2 text-sm font-medium"
          >
            {option}
          </button>
        ) : (
          <button
            key={option}
            onClick={() => onSelect(option)}
            className="bg-surface text-text border border-border rounded-full px-4 py-2 text-sm hover:bg-surface-muted"
          >
            {option}
          </button>
        )
      )}
    </div>
  );
}

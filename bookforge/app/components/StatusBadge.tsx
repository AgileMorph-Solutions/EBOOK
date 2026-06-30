const STATUS_STYLES: Record<string, string> = {
  queued: "bg-amber-100 text-amber-900 border-amber-200",
  running: "bg-blue-100 text-blue-900 border-blue-200",
  done: "bg-moss/15 text-moss border-moss/30",
  failed: "bg-red-100 text-red-900 border-red-200",
  unknown: "bg-gray-100 text-gray-700 border-gray-200",
};

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? STATUS_STYLES.unknown;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${cls}`}
    >
      {status}
    </span>
  );
}

import { Icon } from "@/components/ui/Icon";

type PreviewGridProps = {
  pageCount?: number;
};

export function PreviewGrid({ pageCount = 0 }: PreviewGridProps) {
  const moreCount = Math.max(0, pageCount - 3);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
      <PreviewTile label="Cover" icon="menu_book" />
      <PreviewTile label="Interior spread" icon="auto_stories" />
      <PreviewTile
        label="Typography detail"
        icon="text_fields"
        overlay={moreCount > 0 ? `+ ${moreCount} more` : undefined}
      />
    </div>
  );
}

function PreviewTile({
  label,
  icon,
  overlay,
}: {
  label: string;
  icon: string;
  overlay?: string;
}) {
  return (
    <div className="group relative aspect-[3/4] cursor-pointer overflow-hidden rounded-lg border border-outline/5 bg-surface-container-high editorial-shadow">
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <Icon name={icon} className="text-outline" size={48} />
        <p className="font-label text-label-sm text-secondary">{label}</p>
        <p className="font-body text-body-sm text-outline">Preview coming soon</p>
      </div>
      {overlay && (
        <div className="absolute inset-0 flex items-center justify-center bg-on-surface/20 opacity-0 transition-opacity group-hover:opacity-100">
          <span className="rounded-full bg-white/90 px-4 py-2 font-label text-label-sm backdrop-blur">
            {overlay}
          </span>
        </div>
      )}
    </div>
  );
}

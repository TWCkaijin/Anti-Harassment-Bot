import MaterialIcon from "./MaterialIcon";

export default function HideOptionsButton({ onHide, label = "隱藏選項" }: { onHide: () => void; label?: string }) {
  return <button type="button" onClick={onHide} className="inline-flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-primary bg-primary px-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
    <MaterialIcon icon="expand_less" size={18} />
    {label}
  </button>;
}

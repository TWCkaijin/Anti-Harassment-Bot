/** Shared conversation-and-embrace mark; the adjacent brand text is its label. */
export default function BrandMark({ size = 48, className = "" }: { size?: number; className?: string }) {
  return <img src="/brand-mark.svg" alt="" aria-hidden="true" data-brand-mark width={size} height={size} draggable={false} className={`shrink-0 ${className}`} />;
}

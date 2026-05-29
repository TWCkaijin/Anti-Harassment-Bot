/**
 * MaterialIcon — Material Symbols Outlined 圖示包裝元件
 * 提供型別安全的 icon name 與可調尺寸。
 */

interface MaterialIconProps {
  icon: string;
  size?: number;
  filled?: boolean;
  className?: string;
}

export default function MaterialIcon({
  icon,
  size = 24,
  filled = false,
  className = "",
}: MaterialIconProps) {
  return (
    <span
      className={`material-symbols-outlined ${className}`}
      style={{
        fontSize: `${size}px`,
        fontVariationSettings: filled
          ? "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24"
          : undefined,
      }}
    >
      {icon}
    </span>
  );
}

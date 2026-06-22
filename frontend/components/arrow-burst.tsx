// Burst decorativo (dourado) usado no login e no empty de Favoritos.
export function ArrowBurst({
  size = 120,
  gradId = "lgArrow",
  baseFill = "#26262B",
  className,
  style,
}: {
  size?: number;
  gradId?: string;
  baseFill?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const rots = [0, 45, 90, 135, 180, 270, 315];
  return (
    <svg
      className={className ? `arrowburst ${className}` : "arrowburst"}
      width={size}
      height={size}
      viewBox="0 0 200 200"
      aria-hidden="true"
      style={style}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FFD75E" />
          <stop offset="1" stopColor="#E0A800" />
        </linearGradient>
      </defs>
      {rots.map((r) => (
        <g key={r} transform={`rotate(${r} 100 100)`}>
          <path d="M126,100 L148,84 L148,92 L190,92 L190,108 L148,108 L148,116 Z" fill={baseFill} />
        </g>
      ))}
      <g transform="rotate(225 100 100)">
        <path
          d="M126,100 L148,84 L148,92 L190,92 L190,108 L148,108 L148,116 Z"
          fill={`url(#${gradId})`}
        />
      </g>
    </svg>
  );
}

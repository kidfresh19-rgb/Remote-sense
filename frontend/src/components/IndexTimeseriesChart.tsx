import { useMemo, useState, useRef, useEffect } from "react";
import { motion } from "motion/react";
import { MagnifyingGlassMinus } from "@phosphor-icons/react";

import { dateValue, formatDate, formatDateShort, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, indexMeta } from "@/lib/indices";
import { useTimeseries } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { ErrorState, LoadingRows } from "./states";
import { Button } from "./ui";

const W = 500;
const H = 280;
const PAD = { l: 40, r: 15, t: 20, b: 35 };

export function IndexTimeseriesChart({
  fieldId,
  collecting = false,
  onCollect,
}: {
  fieldId: string;
  collecting?: boolean;
  onCollect?: () => void;
}) {
  const { index, passDate, setPassDate } = useWorkspace();
  const meta = indexMeta(index);
  const query = useTimeseries(fieldId, index, collecting);

  const points = useMemo(
    () => (query.data ?? []).filter((p) => p.mean !== null),
    [query.data],
  );

  // States for interaction
  const [hoveredPoint, setHoveredPoint] = useState<typeof points[0] | null>(null);
  const [zoomRange, setZoomRange] = useState<[number, number] | null>(null);
  const [brushStart, setBrushStart] = useState<number | null>(null);
  const [brushCurrent, setBrushCurrent] = useState<number | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);

  // Reset zoom if the index or field changes
  useEffect(() => {
    setZoomRange(null);
    setHoveredPoint(null);
  }, [index, fieldId]);

  if (query.isLoading) return <LoadingRows rows={4} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!points.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm font-medium text-fg">{`No ${meta.label} history`}</p>
        <p className="max-w-[42ch] text-xs leading-relaxed text-muted">
          {collecting
            ? "Collecting passes. Results appear here as each scene is processed."
            : "No usable passes yet for this field at the current geometry version."}
        </p>
        {onCollect ? (
          <Button variant="primary" onClick={onCollect} disabled={collecting}>
            {collecting ? "Collecting..." : "Collect now"}
          </Button>
        ) : null}
      </div>
    );
  }

  // Filter points based on zoom range. Plain computation, NOT useMemo: this sits below the
  // loading/error/empty early returns, so making it a hook would change the hook count between the
  // loading render and the loaded render and crash with "rendered more hooks than the previous
  // render" (Rules of Hooks). Same applies to bandPath and xTicks below.
  const zoomedPoints = (() => {
    if (!zoomRange) return points;
    return points.filter((p) => {
      const val = dateValue(p.pass_date);
      return val >= zoomRange[0] && val <= zoomRange[1];
    });
  })();

  const xs = zoomedPoints.map((p) => dateValue(p.pass_date));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = maxX - minX || 1;

  const lo = Math.min(meta.min, ...zoomedPoints.map((p) => p.p10 ?? p.mean ?? meta.min));
  const hi = Math.max(meta.max, ...zoomedPoints.map((p) => p.p90 ?? p.mean ?? meta.max));
  const spanY = hi - lo || 1;

  const sx = (v: number) => PAD.l + ((v - minX) / spanX) * (W - PAD.l - PAD.r);
  const sy = (v: number) => PAD.t + (1 - (v - lo) / spanY) * (H - PAD.t - PAD.b);

  // Convert viewBox X coordinate to date value
  const xToDateValue = (vx: number) => {
    const ratio = (vx - PAD.l) / (W - PAD.l - PAD.r);
    return minX + ratio * spanX;
  };

  const meanPath = zoomedPoints.length > 0
    ? zoomedPoints
        .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(xs[i]).toFixed(1)} ${sy(p.mean!).toFixed(1)}`)
        .join(" ")
    : "";

  const bandPath = (() => {
    if (zoomedPoints.length === 0) return "";
    const band: string[] = [];
    for (let i = 0; i < zoomedPoints.length; i++) {
      band.push(`${sx(xs[i]).toFixed(1)},${sy(zoomedPoints[i].p90 ?? zoomedPoints[i].mean!).toFixed(1)}`);
    }
    for (let i = zoomedPoints.length - 1; i >= 0; i--) {
      band.push(`${sx(xs[i]).toFixed(1)},${sy(zoomedPoints[i].p10 ?? zoomedPoints[i].mean!).toFixed(1)}`);
    }
    return `M ${band.join(" L ")} Z`;
  })();

  const selectedPoint = points.find((p) => p.pass_date === passDate) ?? points[points.length - 1];
  const active = hoveredPoint ?? selectedPoint;

  const yTicks = [lo, lo + spanY * 0.25, lo + spanY * 0.5, lo + spanY * 0.75, hi];

  // Distributed X ticks based on dates. Plain computation, not useMemo (hook-order reason above).
  const xTicks = (() => {
    if (zoomedPoints.length < 2) return zoomedPoints;
    const step = Math.max(1, Math.floor(zoomedPoints.length / 4));
    const ticks = [];
    for (let i = 0; i < zoomedPoints.length; i += step) {
      ticks.push(zoomedPoints[i]);
    }
    if (ticks[ticks.length - 1].pass_date !== zoomedPoints[zoomedPoints.length - 1].pass_date) {
      ticks.push(zoomedPoints[zoomedPoints.length - 1]);
    }
    return ticks;
  })();

  // Mouse handlers
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current || zoomedPoints.length === 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * W;

    // Track brush drag
    if (brushStart !== null) {
      setBrushCurrent(Math.max(PAD.l, Math.min(W - PAD.r, vx)));
    }

    // Find nearest point
    let closest = null;
    let minDist = Infinity;
    zoomedPoints.forEach((p) => {
      const px = sx(dateValue(p.pass_date));
      const dist = Math.abs(px - vx);
      if (dist < minDist) {
        minDist = dist;
        closest = p;
      }
    });

    if (minDist < 40) {
      setHoveredPoint(closest);
    } else {
      setHoveredPoint(null);
    }
  };

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * W;

    if (vx >= PAD.l && vx <= W - PAD.r) {
      setBrushStart(vx);
      setBrushCurrent(vx);
    }
  };

  const handleMouseUp = () => {
    if (brushStart !== null && brushCurrent !== null) {
      const diff = Math.abs(brushCurrent - brushStart);
      if (diff > 10) {
        const v1 = xToDateValue(Math.min(brushStart, brushCurrent));
        const v2 = xToDateValue(Math.max(brushStart, brushCurrent));
        setZoomRange([v1, v2]);
      }
    }
    setBrushStart(null);
    setBrushCurrent(null);
  };

  const handleMouseLeave = () => {
    setHoveredPoint(null);
    setBrushStart(null);
    setBrushCurrent(null);
  };

  const handleDoubleClick = () => {
    setZoomRange(null);
  };

  return (
    <div className="p-3 select-none">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-fg uppercase tracking-wider">{meta.long}</h3>
        {zoomRange && (
          <button
            onClick={() => setZoomRange(null)}
            className="flex items-center gap-1 text-[10px] font-medium text-accent hover:opacity-80 transition-opacity"
            title="Reset Zoom (Double click chart)"
          >
            <MagnifyingGlassMinus size={12} />
            <span>Reset zoom</span>
          </button>
        )}
      </div>

      <div className="relative border border-border/40 rounded-lg overflow-hidden bg-panel-2/30 p-2">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-[220px] cursor-crosshair overflow-visible"
          role="img"
          aria-label={`${meta.long} time series`}
          onMouseMove={handleMouseMove}
          onMouseDown={handleMouseDown}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseLeave}
          onDoubleClick={handleDoubleClick}
        >
          <defs>
            <linearGradient id="band-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Horizontal Grid lines */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line
                x1={PAD.l}
                x2={W - PAD.r}
                y1={sy(v)}
                y2={sy(v)}
                stroke="var(--border)"
                strokeWidth={1}
                strokeDasharray="3 3"
                opacity={0.5}
              />
              <text
                x={PAD.l - 6}
                y={sy(v) + 3}
                textAnchor="end"
                fontSize={8}
                className="fill-[var(--muted)] font-mono tnum"
              >
                {v.toFixed(2)}
              </text>
            </g>
          ))}

          {/* Band Path (Gradient Area) */}
          {bandPath && (
            <motion.path
              d={bandPath}
              animate={{ d: bandPath }}
              transition={{ type: "spring", stiffness: 100, damping: 15 }}
              fill="url(#band-grad)"
            />
          )}

          {/* Mean Path Line */}
          {meanPath && (
            <motion.path
              d={meanPath}
              animate={{ d: meanPath }}
              transition={{ type: "spring", stiffness: 100, damping: 15 }}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}

          {/* Interaction vertical crosshair */}
          {hoveredPoint && (
            <line
              x1={sx(dateValue(hoveredPoint.pass_date))}
              x2={sx(dateValue(hoveredPoint.pass_date))}
              y1={PAD.t}
              y2={H - PAD.b}
              className="chart-crosshair chart-crosshair--active"
            />
          )}

          {/* Brush Zoom Drag Selection Overlay */}
          {brushStart !== null && brushCurrent !== null && (
            <rect
              x={Math.min(brushStart, brushCurrent)}
              y={PAD.t}
              width={Math.abs(brushCurrent - brushStart)}
              height={H - PAD.t - PAD.b}
              className="chart-brush"
            />
          )}

          {/* Dots */}
          {zoomedPoints.map((p, i) => {
            const isActive = p.pass_date === passDate;
            const isHovered = hoveredPoint && p.pass_date === hoveredPoint.pass_date;
            const cx = sx(xs[i]);
            const cy = sy(p.mean!);

            return (
              <g
                key={p.pass_date}
                className="cursor-pointer"
                onClick={() => setPassDate(p.pass_date)}
              >
                <circle
                  cx={cx}
                  cy={cy}
                  r={isActive ? 7 : isHovered ? 5.5 : 4}
                  fill={colorForValue(meta, p.mean!)}
                  stroke="var(--panel)"
                  strokeWidth={isActive ? 2 : 1}
                  className={`chart-dot ${isHovered ? "chart-dot--hover" : ""}`}
                  opacity={0.6 + 0.4 * p.clear_fraction}
                />
              </g>
            );
          })}

          {/* X Axis Ticks */}
          {xTicks.map((p, i) => {
            const x = sx(dateValue(p.pass_date));
            return (
              <g key={i}>
                <line
                  x1={x}
                  x2={x}
                  y1={H - PAD.b}
                  y2={H - PAD.b + 4}
                  stroke="var(--border)"
                  strokeWidth={1}
                />
                <text
                  x={x}
                  y={H - PAD.b + 12}
                  textAnchor="middle"
                  fontSize={8}
                  className="fill-[var(--muted)] font-mono"
                >
                  {formatDateShort(p.pass_date)}
                </text>
              </g>
            );
          })}
        </svg>

        {/* Hover / Active Tooltip Glassmorphic overlay */}
        {hoveredPoint && (
          <div
            className="absolute z-10 chart-tooltip px-2.5 py-2 text-[10px] leading-relaxed flex flex-col gap-1 w-32 shadow-lg"
            style={{
              left: `${Math.min(80, Math.max(2, (sx(dateValue(hoveredPoint.pass_date)) / W) * 100 - 15))}%`,
              top: `${Math.min(70, Math.max(2, (sy(hoveredPoint.mean!) / H) * 100 - 30))}%`,
            }}
          >
            <div className="font-semibold border-b border-border/40 pb-0.5 mb-0.5 text-fg">
              {formatDate(hoveredPoint.pass_date)}
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Mean:</span>
              <span className="font-mono font-medium text-fg">{formatNumber(hoveredPoint.mean)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Range:</span>
              <span className="font-mono text-muted">{formatNumber(hoveredPoint.p10)}..{formatNumber(hoveredPoint.p90)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Clear:</span>
              <span className="font-mono text-fg">{formatPercent(hoveredPoint.clear_fraction)}</span>
            </div>
          </div>
        )}
      </div>

      {/* Redesigned Stats cards panel below */}
      <dl className="mt-3 grid grid-cols-3 gap-2">
        <Stat
          label={`Mean ${meta.label}`}
          value={formatNumber(active.mean)}
          activeKey={active.pass_date + active.mean}
        />
        <Stat
          label="Clear Fraction"
          value={formatPercent(active.clear_fraction)}
          activeKey={active.pass_date + active.clear_fraction}
        />
        <Stat
          label="Confidence"
          value={active.confidence ?? "·"}
          activeKey={active.pass_date + active.confidence}
          textAccent={active.confidence === "high" ? "text-positive" : active.confidence === "low" ? "text-critical" : "text-caution"}
        />
      </dl>

      <div className="mt-2.5 flex items-center justify-between text-[11px] text-muted font-medium border-t border-border/40 pt-2 px-1">
        <span>{formatDate(active.pass_date)}</span>
        <div className="flex gap-2">
          <span>p10: <span className="font-mono text-fg">{formatNumber(active.p10)}</span></span>
          <span>p90: <span className="font-mono text-fg">{formatNumber(active.p90)}</span></span>
        </div>
      </div>
    </div>
  );
}

interface StatProps {
  label: string;
  value: string;
  activeKey: string;
  textAccent?: string;
}

function Stat({ label, value, activeKey, textAccent }: StatProps) {
  return (
    <div className="stat-card px-3 py-2 flex flex-col justify-between min-w-0">
      <dt className="truncate text-[9px] font-bold uppercase tracking-wider text-muted mb-0.5">{label}</dt>
      <motion.dd
        key={activeKey}
        initial={{ opacity: 0, y: 3 }}
        animate={{ opacity: 1, y: 0 }}
        className={`text-sm font-semibold truncate tnum ${textAccent ?? "text-fg"}`}
      >
        {value}
      </motion.dd>
    </div>
  );
}

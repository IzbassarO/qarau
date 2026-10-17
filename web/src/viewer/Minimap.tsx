import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import type { Vector3 } from 'three';
import type { Bundle } from '../lib/bundle';
import { computeMinimapTransform, viewConePolygon } from './minimap';

const SIZE = 180;
const MAX_POINTS = 20_000;

export interface MinimapState {
  position: Vector3;
  yaw: number;
  /** Horizontal camera field of view in degrees for the top-view cone. */
  fov: number;
}

export interface MinimapHandle {
  update: (state: MinimapState) => void;
}

function context(canvas: HTMLCanvasElement | null): CanvasRenderingContext2D | null {
  // A DOM without Canvas 2D (including headless unit tests) still gets the label.
  if (!canvas || typeof CanvasRenderingContext2D === 'undefined') return null;
  return canvas.getContext('2d');
}

/** Static cloud and animated pose use separate canvases to avoid repainting points. */
export const Minimap = forwardRef<MinimapHandle, { bundle: Bundle; label: string }>(
  function Minimap({ bundle, label }, ref) {
    const cloud = useRef<HTMLCanvasElement>(null);
    const pose = useRef<HTMLCanvasElement>(null);
    const poseContext = useRef<CanvasRenderingContext2D | null>(null);
    const transform = useMemo(() => {
      const { min, max } = bundle.manifest.world.bounds;
      // A square extent preserves angles and scale; the margin keeps edge points visible.
      const extent = Math.max(max[0] - min[0], max[1] - min[1], 1e-6) * 1.12;
      const cx = (max[0] + min[0]) / 2;
      const cy = (max[1] + min[1]) / 2;
      return computeMinimapTransform(
        {
          min: { x: cx - extent / 2, y: cy - extent / 2 },
          max: { x: cx + extent / 2, y: cy + extent / 2 },
        },
        SIZE,
      );
    }, [bundle]);

    useEffect(() => {
      const ctx = context(cloud.current);
      poseContext.current = context(pose.current);
      if (!ctx) return;
      ctx.clearRect(0, 0, SIZE, SIZE);
      ctx.fillStyle = '#05080b';
      ctx.fillRect(0, 0, SIZE, SIZE);
      ctx.fillStyle = '#7eb2d9';
      ctx.globalAlpha = 0.6;
      const count = Math.floor(bundle.position.length / 3);
      const stride = Math.max(1, Math.ceil(count / MAX_POINTS));
      for (let i = 0; i < count; i += stride) {
        const x = bundle.position[i * 3];
        const y = bundle.position[i * 3 + 1];
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        const p = transform.worldToPixel({ x, y });
        ctx.fillRect(p.x, p.y, 1, 1);
      }
      ctx.globalAlpha = 1;
      return () => {
        poseContext.current = null;
      };
    }, [bundle, transform]);

    useImperativeHandle(
      ref,
      () => ({
        update(state) {
          const ctx = poseContext.current;
          if (!ctx) return;
          ctx.clearRect(0, 0, SIZE, SIZE);
          const projected = transform.worldToPixel(state.position);
          // Keep the circle visible when the free camera leaves the mapped footprint.
          const p = {
            x: Math.max(7, Math.min(SIZE - 7, projected.x)),
            y: Math.max(7, Math.min(SIZE - 7, projected.y)),
          };
          const polygon = viewConePolygon(p, state.yaw, (state.fov * Math.PI) / 180, 30);
          ctx.beginPath();
          ctx.moveTo(polygon[0].x, polygon[0].y);
          ctx.lineTo(polygon[1].x, polygon[1].y);
          ctx.lineTo(polygon[2].x, polygon[2].y);
          ctx.closePath();
          ctx.fillStyle = 'rgba(108, 181, 255, 0.2)';
          ctx.fill();
          ctx.strokeStyle = '#78baff';
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
          ctx.fillStyle = '#080d13';
          ctx.fill();
          ctx.strokeStyle = '#f2f7ff';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        },
      }),
      [transform],
    );

    return (
      <div
        role="img"
        aria-label={label}
        data-testid="observer-minimap"
        className="pointer-events-none absolute bottom-3 right-3 overflow-hidden border border-[#586c80] bg-[#05080b]"
        style={{ width: SIZE, height: SIZE }}
      >
        <canvas ref={cloud} width={SIZE} height={SIZE} aria-hidden="true" />
        <canvas
          ref={pose}
          width={SIZE}
          height={SIZE}
          aria-hidden="true"
          className="absolute inset-0"
        />
        <span className="absolute left-1.5 top-1 bg-[#05080b]/80 px-1 font-mono text-[10px] text-[#b9d5ee]">
          {label}
        </span>
      </div>
    );
  },
);

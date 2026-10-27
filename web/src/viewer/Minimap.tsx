import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import type { Vector3 } from 'three';
import type { Bundle } from '../lib/bundle';
import tokens from '../design/tokens.json' with { type: 'json' };
import { computeMinimapTransform, viewConePolygon } from './minimap';

// Fixed canvas backing resolution; v8 has no separate minimap layout token.
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
      ctx.fillStyle = tokens.map.ground;
      ctx.fillRect(0, 0, SIZE, SIZE);
      ctx.fillStyle = tokens.map.text;
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
          ctx.fillStyle = tokens.map.text;
          ctx.globalAlpha = 0.2;
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.strokeStyle = tokens.map.text;
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
          ctx.fillStyle = tokens.map.ground;
          ctx.fill();
          ctx.strokeStyle = tokens.map.text;
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
        className="qv-float qv-float--map viewer-minimap"
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
        <span className="viewer-minimap-label">{label}</span>
      </div>
    );
  },
);

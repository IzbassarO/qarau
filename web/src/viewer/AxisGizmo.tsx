import { forwardRef, useImperativeHandle, useRef } from 'react';
import { Quaternion, Vector3 } from 'three';
import { useI18n, type Key } from '../i18n';
import type { Axis } from './gizmo';

const axes: { axis: Axis; label: Key; name: Key; color: string; direction: number[] }[] = [
  { axis: 'x', label: 'gizmo.x', name: 'gizmo.positiveX', color: '#FF6B6B', direction: [1, 0, 0] },
  {
    axis: '-x',
    label: 'gizmo.negativeXSymbol',
    name: 'gizmo.negativeX',
    color: '#FF6B6B',
    direction: [-1, 0, 0],
  },
  { axis: 'y', label: 'gizmo.y', name: 'gizmo.positiveY', color: '#6BE675', direction: [0, 1, 0] },
  {
    axis: '-y',
    label: 'gizmo.negativeYSymbol',
    name: 'gizmo.negativeY',
    color: '#6BE675',
    direction: [0, -1, 0],
  },
  { axis: 'z', label: 'gizmo.z', name: 'gizmo.positiveZ', color: '#6BA8FF', direction: [0, 0, 1] },
  {
    axis: '-z',
    label: 'gizmo.negativeZSymbol',
    name: 'gizmo.negativeZ',
    color: '#6BA8FF',
    direction: [0, 0, -1],
  },
];

export interface AxisGizmoHandle {
  update: (orientation: Quaternion) => void;
}

/** Project the world axes into camera space without a React render on every frame. */
export const AxisGizmo = forwardRef<AxisGizmoHandle, { onSelect: (axis: Axis) => void }>(
  function AxisGizmo({ onSelect }, ref) {
    const { t } = useI18n();
    const buttons = useRef(new Map<Axis, HTMLButtonElement>());
    const lines = useRef(new Map<Axis, SVGLineElement>());
    useImperativeHandle(ref, () => ({
      update(orientation) {
        const inverse = orientation.clone().invert();
        for (const { axis, direction } of axes) {
          const p = new Vector3(...direction).applyQuaternion(inverse);
          let x = p.x * 35;
          const y = -p.y * 35;
          // When looking exactly down an axis, keep both centre buttons clickable.
          if (Math.hypot(x, y) < 12) x += p.z >= 0 ? 12 : -12;
          const button = buttons.current.get(axis);
          if (button) {
            button.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
            button.style.zIndex = String(Math.round((p.z + 1) * 10));
            button.style.opacity = p.z < -0.15 ? '0.65' : '1';
          }
          const line = lines.current.get(axis);
          line?.setAttribute('x2', String(52 + x));
          line?.setAttribute('y2', String(52 + y));
        }
      },
    }));

    return (
      <div
        role="group"
        aria-label={t('gizmo.label')}
        className="absolute bottom-3 left-3 size-[104px] rounded-full bg-bg/45"
      >
        <svg className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
          {axes.map(({ axis, color }) => (
            <line
              key={axis}
              ref={(el) => {
                if (el) lines.current.set(axis, el);
                else lines.current.delete(axis);
              }}
              x1="52"
              y1="52"
              x2="52"
              y2="52"
              stroke={color}
              strokeWidth="1.5"
              opacity={axis.startsWith('-') ? 0.35 : 0.8}
            />
          ))}
        </svg>
        {axes.map(({ axis, label, name, color, direction }) => (
          <button
            key={axis}
            ref={(el) => {
              if (el) buttons.current.set(axis, el);
              else buttons.current.delete(axis);
            }}
            type="button"
            aria-label={t(name)}
            title={t(name)}
            onClick={() => onSelect(axis)}
            className="absolute left-1/2 top-1/2 flex size-[22px] cursor-pointer items-center justify-center rounded-full border font-mono text-[10px] font-semibold shadow-sm outline-offset-2 hover:brightness-125 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
            style={{
              borderColor: color,
              background: axis.startsWith('-') ? '#15191F' : color,
              color: axis.startsWith('-') ? color : '#0B0C0E',
              transform: `translate(${direction[0] * 35 || direction[2] * 12}px, ${-direction[1] * 35}px) translate(-50%, -50%)`,
            }}
          >
            {t(label)}
          </button>
        ))}
      </div>
    );
  },
);

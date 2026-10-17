import { forwardRef, useImperativeHandle, useRef } from 'react';
import { Quaternion } from 'three';
import { useI18n, type Key } from '../i18n';
import { GIZMO_BUTTON_SIZE, GIZMO_SIZE, projectAxisButtons, type Axis } from './gizmo';

const axes: { axis: Axis; label: Key; name: Key; color: string }[] = [
  { axis: 'x', label: 'gizmo.x', name: 'gizmo.positiveX', color: '#FF6B6B' },
  {
    axis: '-x',
    label: 'gizmo.negativeXSymbol',
    name: 'gizmo.negativeX',
    color: '#FF6B6B',
  },
  { axis: 'y', label: 'gizmo.y', name: 'gizmo.positiveY', color: '#6BE675' },
  {
    axis: '-y',
    label: 'gizmo.negativeYSymbol',
    name: 'gizmo.negativeY',
    color: '#6BE675',
  },
  { axis: 'z', label: 'gizmo.z', name: 'gizmo.positiveZ', color: '#6BA8FF' },
  {
    axis: '-z',
    label: 'gizmo.negativeZSymbol',
    name: 'gizmo.negativeZ',
    color: '#6BA8FF',
  },
];

const initialPositions = new Map(
  projectAxisButtons(new Quaternion()).map(({ axis, x, y }) => [axis, { x, y }]),
);

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
        for (const { axis, x, y, depth } of projectAxisButtons(orientation)) {
          const button = buttons.current.get(axis);
          if (button) {
            button.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
            button.style.zIndex = String(Math.round((depth + 1) * 10));
            button.style.opacity = depth < -0.15 ? '0.65' : '1';
          }
          const line = lines.current.get(axis);
          line?.setAttribute('x2', String(GIZMO_SIZE / 2 + x));
          line?.setAttribute('y2', String(GIZMO_SIZE / 2 + y));
        }
      },
    }));

    return (
      <div
        role="group"
        aria-label={t('gizmo.label')}
        className="absolute bottom-3 left-3 rounded-full bg-bg/45"
        style={{ width: GIZMO_SIZE, height: GIZMO_SIZE }}
      >
        <svg className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
          {axes.map(({ axis, color }) => (
            <line
              key={axis}
              ref={(el) => {
                if (el) lines.current.set(axis, el);
                else lines.current.delete(axis);
              }}
              x1={GIZMO_SIZE / 2}
              y1={GIZMO_SIZE / 2}
              x2={GIZMO_SIZE / 2}
              y2={GIZMO_SIZE / 2}
              stroke={color}
              strokeWidth="1.5"
              opacity={axis.startsWith('-') ? 0.35 : 0.8}
            />
          ))}
        </svg>
        {axes.map(({ axis, label, name, color }) => (
          <button
            key={axis}
            ref={(el) => {
              if (el) buttons.current.set(axis, el);
              else buttons.current.delete(axis);
            }}
            type="button"
            disabled={axis === '-z'}
            aria-label={t(name)}
            title={t(axis === '-z' ? 'gizmo.groundLimited' : name)}
            onClick={() => onSelect(axis)}
            className="absolute left-1/2 top-1/2 flex cursor-pointer items-center justify-center rounded-full border font-mono text-[10px] font-semibold shadow-sm outline-offset-2 hover:brightness-125 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white disabled:cursor-not-allowed disabled:opacity-40"
            style={{
              width: GIZMO_BUTTON_SIZE,
              height: GIZMO_BUTTON_SIZE,
              borderColor: color,
              background: axis.startsWith('-') ? '#15191F' : color,
              color: axis.startsWith('-') ? color : '#0B0C0E',
              transform: `translate(${initialPositions.get(axis)!.x}px, ${initialPositions.get(axis)!.y}px) translate(-50%, -50%)`,
            }}
          >
            {t(label)}
          </button>
        ))}
      </div>
    );
  },
);

import { forwardRef, useImperativeHandle, useRef } from 'react';
import { Quaternion } from 'three';
import { useI18n, type Key } from '../i18n';
import { GIZMO_BUTTON_SIZE, GIZMO_SIZE, projectAxisButtons, type Axis } from './gizmo';

const axes: { axis: Axis; label: Key; name: Key }[] = [
  { axis: 'x', label: 'gizmo.x', name: 'gizmo.positiveX' },
  {
    axis: '-x',
    label: 'gizmo.negativeXSymbol',
    name: 'gizmo.negativeX',
  },
  { axis: 'y', label: 'gizmo.y', name: 'gizmo.positiveY' },
  {
    axis: '-y',
    label: 'gizmo.negativeYSymbol',
    name: 'gizmo.negativeY',
  },
  { axis: 'z', label: 'gizmo.z', name: 'gizmo.positiveZ' },
  {
    axis: '-z',
    label: 'gizmo.negativeZSymbol',
    name: 'gizmo.negativeZ',
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
        className="viewer-gizmo"
        style={{ width: GIZMO_SIZE, height: GIZMO_SIZE }}
      >
        <svg className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
          {axes.map(({ axis }) => (
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
              stroke="currentColor"
              strokeWidth="1.5"
              opacity={axis.startsWith('-') ? 0.35 : 0.8}
            />
          ))}
        </svg>
        {axes.map(({ axis, label, name }) => (
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
            className="viewer-axis"
            style={{
              width: GIZMO_BUTTON_SIZE,
              height: GIZMO_BUTTON_SIZE,
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

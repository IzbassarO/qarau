import { framesForDensePoint, runUrl, type Bundle, type FrameInfo } from '../lib/bundle';
import { quatToMat3 } from '../lib/geometry';

type Measurement<R extends string> =
  { available: false; reason: R } | { available: true; value: number };

export interface PointInfo {
  index: number;
  position: { x: number; y: number; z: number };
  unit: 'm' | 'model';
  frames: { frameId: number; thumbnailUrl: string; timeSec: number | null }[];
  views: number;
  visibilitySource: 'track' | 'estimated' | 'colmap_fusion' | 'sparse_tracks' | 'unknown';
  /** The fake densifier supplies synthetic test points, not a reconstruction. */
  synthetic?: boolean;
  /** Signed height along world-up, relative to the model origin (not ground). */
  height: Measurement<'needs_scale'>;
  /** Euclidean distance from the model origin. */
  distance: Measurement<'needs_scale'>;
  confidence: Measurement<'needs_confidence'>;
}

/** Only a finite, positive, explicitly declared metric calibration enables metres. */
export function metricScale(bundle: Bundle): number | null {
  const scale = bundle.manifest.world.scale_m_per_unit;
  return typeof scale === 'number' && Number.isFinite(scale) && scale > 0 ? scale : null;
}

export function frameTime(frame: FrameInfo): number | null {
  return typeof frame.t === 'number' && Number.isFinite(frame.t) && frame.t >= 0 ? frame.t : null;
}

function inFrame(point: number[], frame: FrameInfo): boolean {
  const { camera, pose } = frame;
  if (!frame.registered || !camera || !pose) return false;
  if (!['SIMPLE_PINHOLE', 'PINHOLE', 'SIMPLE_RADIAL', 'RADIAL'].includes(camera.model))
    return false;
  const r = quatToMat3(pose.qvec);
  const [x, y, z] = point.map(
    (_, row) =>
      r[row * 3] * point[0] +
      r[row * 3 + 1] * point[1] +
      r[row * 3 + 2] * point[2] +
      pose.tvec[row],
  );
  if (!(z > 0)) return false;
  let u = x / z;
  let v = y / z;
  // Match export.py's supported COLMAP models, including radial distortion.
  if (camera.model === 'SIMPLE_RADIAL' || camera.model === 'RADIAL') {
    const r2 = u * u + v * v;
    const k1 = camera.params[3];
    const k2 = camera.model === 'RADIAL' ? camera.params[4] : 0;
    const distortion = 1 + k1 * r2 + k2 * r2 * r2;
    u *= distortion;
    v *= distortion;
  }
  const [fx, fy, cx, cy] = camera.K;
  if (!(fx > 0 && fy > 0)) return false;
  const px = fx * u + cx;
  const py = fy * v + cy;
  return px >= 0 && px < camera.width && py >= 0 && py < camera.height;
}

/** Build evidence for one object-cloud index; never infer scale, confidence or FPS. */
export function buildPointInfo(bundle: Bundle, pointIndex: number): PointInfo {
  if (
    !Number.isSafeInteger(pointIndex) ||
    pointIndex < 0 ||
    pointIndex >= bundle.manifest.points.count ||
    pointIndex * 3 + 2 >= bundle.position.length
  )
    throw new RangeError('Point index is outside the bundle');

  const point = Array.from(bundle.position.subarray(pointIndex * 3, pointIndex * 3 + 3));
  const { pointOffsets, pointFrames, frameOffsets, frameInPoints } = bundle;
  const start = pointOffsets[pointIndex];
  const end = pointOffsets[pointIndex + 1];
  const hasFrameLinks =
    frameOffsets.length === bundle.manifest.frames.length + 1 &&
    frameOffsets[0] === 0 &&
    frameOffsets[frameOffsets.length - 1] === frameInPoints.length &&
    frameOffsets.every((offset, i) => i === 0 || offset >= frameOffsets[i - 1]) &&
    (!!bundle.manifest.tracks || frameInPoints.length > 0);
  let indices: number[];
  let visibilitySource: PointInfo['visibilitySource'];
  if (pointOffsets.length > pointIndex + 1 && start <= end && end <= pointFrames.length) {
    indices = Array.from(pointFrames.subarray(start, end));
    visibilitySource = 'track';
  } else if (hasFrameLinks) {
    // Also honour bundles that retain only the reverse, frame -> point links.
    indices = bundle.manifest.frames.flatMap((_, i) =>
      frameInPoints.subarray(frameOffsets[i], frameOffsets[i + 1]).includes(pointIndex) ? [i] : [],
    );
    visibilitySource = 'track';
  } else {
    indices = bundle.manifest.frames.flatMap((frame, i) => (inFrame(point, frame) ? [i] : []));
    visibilitySource = 'estimated';
  }
  const confidence =
    bundle.pointConfidence?.length === bundle.manifest.points.count
      ? bundle.pointConfidence[pointIndex]
      : undefined;
  return pointInfo(bundle, pointIndex, point, indices, visibilitySource, confidence);
}

/** Dense evidence uses its declared CSR rows only, including intentionally empty rows. */
export function buildDensePointInfo(bundle: Bundle, pointIndex: number): PointInfo {
  const layer = bundle.dense;
  if (
    !layer ||
    !Number.isSafeInteger(pointIndex) ||
    pointIndex < 0 ||
    pointIndex >= layer.count ||
    pointIndex * 3 + 2 >= layer.position.length
  )
    throw new RangeError('Dense point index is outside the bundle');

  const source = layer.frameLinksSource;
  return {
    ...pointInfo(
      bundle,
      pointIndex,
      Array.from(layer.position.subarray(pointIndex * 3, pointIndex * 3 + 3)),
      framesForDensePoint(layer, pointIndex),
      source === 'colmap_fusion' || source === 'sparse_tracks' ? source : 'unknown',
    ),
    synthetic: layer.densifier === 'fake',
  };
}

function pointInfo(
  bundle: Bundle,
  pointIndex: number,
  point: number[],
  indices: Iterable<number>,
  visibilitySource: PointInfo['visibilitySource'],
  confidence?: number,
): PointInfo {
  const frames = [...new Set(indices)].flatMap((i) => {
    const frame = bundle.manifest.frames[i];
    return frame
      ? [
          {
            frameId: frame.i,
            thumbnailUrl: bundle.assetUrl?.(frame.thumb) ?? runUrl(bundle.runId, frame.thumb),
            timeSec: frameTime(frame),
          },
        ]
      : [];
  });
  const scale = metricScale(bundle);
  const position = point.map((value) => value * (scale ?? 1));
  const up = bundle.manifest.world.up;
  const upLength = Math.hypot(...up);
  const height = position.reduce((sum, value, axis) => sum + value * up[axis], 0) / upLength;
  return {
    index: pointIndex,
    position: { x: position[0], y: position[1], z: position[2] },
    unit: scale === null ? 'model' : 'm',
    frames,
    views: frames.length,
    visibilitySource,
    height:
      scale !== null && Number.isFinite(height)
        ? { available: true, value: height }
        : { available: false, reason: 'needs_scale' },
    distance:
      scale !== null
        ? { available: true, value: Math.hypot(...position) }
        : { available: false, reason: 'needs_scale' },
    confidence:
      confidence !== undefined && Number.isFinite(confidence) && confidence >= 0 && confidence <= 1
        ? { available: true, value: confidence }
        : { available: false, reason: 'needs_confidence' },
  };
}

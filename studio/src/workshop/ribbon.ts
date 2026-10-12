type Vec = [number, number, number];
const knots: Vec[] = [[-1.65, 1.05, -.25], [-.15, 1.25, -.4], [1.25, .85, -.15], [1.1, .2, .7], [-.95, -.15, .65], [-1.25, -.65, -.15], [-.25, -1.2, -.45], [1.65, -1.05, .05]];
const subtract = (a: Vec, b: Vec): Vec => a.map((v, i) => v - b[i]) as Vec;
const cross = (a: Vec, b: Vec): Vec => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: Vec): Vec => { const length = Math.hypot(...a); return a.map((v) => v / Math.max(length, 1e-9)) as Vec; };

export function ribbonPath(t: number, open = false): Vec {
  t = Math.max(0, Math.min(1, t));
  if (open) return [4.8 * (t - .5), .45 * Math.cos(t * Math.PI * 2), .25 * Math.sin(t * Math.PI * 4)];
  const segment = Math.min(knots.length - 2, Math.floor(t * (knots.length - 1)));
  const u = t * (knots.length - 1) - segment;
  const [a, b, c, d] = [knots[Math.max(0, segment - 1)], knots[segment], knots[segment + 1], knots[Math.min(knots.length - 1, segment + 2)]];
  return b.map((v, i) => .5 * (2 * v + (-a[i] + c[i]) * u + (2 * a[i] - 5 * v + 4 * c[i] - d[i]) * u * u + (-a[i] + 3 * v - 3 * c[i] + d[i]) * u ** 3)) as Vec;
}

// Two authored poses share topology: the folded identity becomes an inspection rail.
export function createRibbon(steps = 160): Float32Array {
  if (!Number.isInteger(steps) || steps < 10 || steps > 320) throw new Error("Ribbon resolution must be between 10 and 320.");
  const vertices: number[] = [];
  const profile = [[-.25, -.028], [-.225, -.055], [.225, -.055], [.25, -.028], [.25, .028], [.225, .055], [-.225, .055], [-.25, .028]];
  function ring(t: number, open: boolean): Vec[] {
    const point = ribbonPath(t, open);
    const tangent = unit(subtract(ribbonPath(t + .001, open), ribbonPath(t - .001, open)));
    const side = unit(cross([0, 0, 1], tangent)), normal = unit(cross(tangent, side));
    const twist = open ? .22 * Math.sin(t * Math.PI * 4) : .9 * Math.sin(t * Math.PI * 3) + .2;
    return profile.map(([width, depth]) => point.map((v, i) => v + side[i] * (width * Math.cos(twist) - depth * Math.sin(twist)) + normal[i] * (width * Math.sin(twist) + depth * Math.cos(twist))) as Vec);
  }
  function triangle(folded: Vec[], opened: Vec[], section: number, foldedNormals?: Vec[], openNormals?: Vec[]) {
    const normal = unit(cross(subtract(folded[1], folded[0]), subtract(folded[2], folded[0])));
    const openNormal = unit(cross(subtract(opened[1], opened[0]), subtract(opened[2], opened[0])));
    for (let i = 0; i < 3; i++) vertices.push(...folded[i], ...(foldedNormals?.[i] ?? normal), ...opened[i], ...(openNormals?.[i] ?? openNormal), section);
  }
  const folded = Array.from({ length: steps + 1 }, (_, i) => ring(i / steps, false));
  const opened = Array.from({ length: steps + 1 }, (_, i) => ring(i / steps, true));
  const faceNormal = (pose: Vec[][], i: number, j: number): Vec => unit(cross(subtract(pose[Math.min(steps, i + 1)][j], pose[Math.max(0, i - 1)][j]), subtract(pose[i][(j + 1) % profile.length], pose[i][j])));
  for (let i = 0; i < steps; i++) {
    const a = folded[i], b = folded[i + 1], c = opened[i], d = opened[i + 1];
    for (let j = 0; j < profile.length; j++) {
      const k = (j + 1) % profile.length, section = Math.min(4, Math.floor(i * 5 / steps));
      const n = faceNormal(folded, i, j), m = faceNormal(folded, i + 1, j);
      const p = faceNormal(opened, i, j), q = faceNormal(opened, i + 1, j);
      triangle([a[j], b[j], b[k]], [c[j], d[j], d[k]], section, [n, m, m], [p, q, q]);
      triangle([a[j], b[k], a[k]], [c[j], d[k], c[k]], section, [n, m, n], [p, q, p]);
    }
    if (i === 0 || i === steps - 1) {
      const fold = i === 0 ? a : b, open = i === 0 ? c : d;
      for (let j = 1; j < profile.length - 1; j++) triangle([fold[0], fold[j], fold[j + 1]], [open[0], open[j], open[j + 1]], i === 0 ? 0 : 4);
    }
  }
  return new Float32Array(vertices);
}

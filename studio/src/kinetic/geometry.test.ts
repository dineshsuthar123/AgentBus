import { describe, expect, it } from "vitest";
import { createSlab, multiply, outline, perspective, roundedOutline, transform, triangulate } from "./geometry";

describe("original Syndra inspection geometry", () => {
  it("rounds a bounded concave S without importing a model", () => {
    const points = roundedOutline();
    expect(outline).toHaveLength(14);
    expect(points).toHaveLength(126);
    expect(points.flat().every((value) => Number.isFinite(value) && Math.abs(value) <= .95)).toBe(true);
  });
  it("covers the outline without bridging its concave cutouts", () => {
    const points = roundedOutline();
    const triangles = triangulate(points);
    const polygonArea = Math.abs(points.reduce((sum, a, i) => {
      const b = points[(i + 1) % points.length];
      return sum + a[0] * b[1] - b[0] * a[1];
    }, 0)) / 2;
    const triangleArea = triangles.reduce((sum, [i, j, k]) => {
      const a = points[i], b = points[j], c = points[k];
      return sum + Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2;
    }, 0);
    expect(triangles).toHaveLength(points.length - 2);
    expect(triangleArea).toBeCloseTo(polygonArea, 10);
  });
  it("produces bounded bevels and unit lighting normals", () => {
    const mesh = createSlab();
    expect(mesh.byteLength).toBeLessThan(80_000);
    expect(mesh.length % 18).toBe(0);
    for (let i = 0; i < mesh.length; i += 6) {
      expect(Math.hypot(mesh[i + 3], mesh[i + 4], mesh[i + 5])).toBeCloseTo(1, 5);
      expect(Math.abs(mesh[i + 1])).toBeLessThanOrEqual(.110001);
    }
  });
  it("composes column-major transforms and bounded perspective matrices", () => {
    const moved = transform(1, 2, -7, .3, -.5, 1.2);
    expect([...multiply(transform(), moved)]).toEqual([...moved]);
    for (const aspect of [.4, 1, 2.4]) {
      expect([...perspective(aspect)].every(Number.isFinite)).toBe(true);
      expect(perspective(aspect)[11]).toBe(-1);
    }
  });
});

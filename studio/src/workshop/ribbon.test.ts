import { describe, expect, it } from "vitest";
import { createRibbon, ribbonPath } from "./ribbon";

describe("original two-pose ribbon mesh", () => {
  it("has bounded, finite positions and unit surface normals in both poses", () => {
    const mesh = createRibbon();
    expect(mesh.byteLength).toBeLessThan(500_000);
    expect(mesh.length % 39).toBe(0);
    const regions = new Set<number>();
    for (let i = 0; i < mesh.length; i += 13) {
      for (const value of mesh.slice(i, i + 13)) expect(Number.isFinite(value)).toBe(true);
      expect(Math.hypot(...mesh.slice(i + 3, i + 6))).toBeCloseTo(1, 4);
      expect(Math.hypot(...mesh.slice(i + 9, i + 12))).toBeCloseTo(1, 4);
      expect(Math.max(...mesh.slice(i, i + 3).map(Math.abs))).toBeLessThan(3);
      regions.add(mesh[i + 12]);
    }
    expect([...regions]).toEqual([0, 1, 2, 3, 4]);
  });
  it("unfolds into a genuinely different continuous path, not recolored slabs", () => {
    expect(ribbonPath(.5)).not.toEqual(ribbonPath(.5, true));
    for (const open of [false, true]) for (let i = 1; i <= 100; i++) {
      const a = ribbonPath((i - 1) / 100, open), b = ribbonPath(i / 100, open);
      expect(Math.hypot(...a.map((v, axis) => v - b[axis]))).toBeLessThan(.2);
    }
    expect(ribbonPath(-1)).toEqual(ribbonPath(0));
    expect(ribbonPath(2)).toEqual(ribbonPath(1));
  });
  it("rejects unbounded or invalid mesh allocations", () => {
    for (const steps of [0, 9, 321, Infinity, NaN, 10.5]) expect(() => createRibbon(steps)).toThrow(/resolution/);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KineticEngine } from "./engine";

let intersect: (entries: Array<{ isIntersecting: boolean }>) => void;
let resize: () => void;
let motion: () => void;
let reduced: boolean;
let hidden: boolean;
let frame: FrameRequestCallback | undefined;
let gl: ReturnType<typeof context>;
const disconnect = vi.fn();
const motionRemove = vi.fn();

function context() {
  return {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4,
    ARRAY_BUFFER: 5, STATIC_DRAW: 6, FLOAT: 7, DEPTH_TEST: 8,
    COLOR_BUFFER_BIT: 16, DEPTH_BUFFER_BIT: 32, TRIANGLES: 9,
    createProgram: vi.fn(() => ({})), createShader: vi.fn(() => ({})), createBuffer: vi.fn(() => ({})),
    shaderSource: vi.fn(), compileShader: vi.fn(), attachShader: vi.fn(), deleteShader: vi.fn(),
    getShaderParameter: vi.fn(() => true), getProgramParameter: vi.fn(() => true),
    bindAttribLocation: vi.fn(), linkProgram: vi.fn(), useProgram: vi.fn(),
    bindBuffer: vi.fn(), bufferData: vi.fn(), enableVertexAttribArray: vi.fn(), vertexAttribPointer: vi.fn(),
    getAttribLocation: vi.fn(() => 1), getUniformLocation: vi.fn(() => ({})), enable: vi.fn(), clearColor: vi.fn(),
    viewport: vi.fn(), clear: vi.fn(), uniformMatrix4fv: vi.fn(), uniform3fv: vi.fn(), uniform1f: vi.fn(),
    drawArrays: vi.fn(), flush: vi.fn(), deleteBuffer: vi.fn(), deleteProgram: vi.fn(),
  };
}
function canvas() {
  const target = document.createElement("canvas");
  Object.defineProperties(target, { clientWidth: { value: 300 }, clientHeight: { value: 240 } });
  document.body.append(target);
  return target;
}

beforeEach(() => {
  gl = context(); reduced = false; hidden = false; frame = undefined;
  disconnect.mockClear(); motionRemove.mockClear();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(gl as unknown as WebGLRenderingContext);
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  vi.stubGlobal("devicePixelRatio", 3);
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => { frame = callback; return 1; }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn(() => { frame = undefined; }));
  vi.stubGlobal("matchMedia", () => ({ get matches() { return reduced; }, addEventListener: (_: string, callback: () => void) => { motion = callback; }, removeEventListener: motionRemove }));
  vi.stubGlobal("IntersectionObserver", class { constructor(callback: typeof intersect) { intersect = callback; } observe() {} disconnect = disconnect; });
  vi.stubGlobal("ResizeObserver", class { constructor(callback: typeof resize) { resize = callback; } observe() {} disconnect = disconnect; });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("bounded inspection renderer", () => {
  it("bounds direct orbit input and accepts explicit poses without ambient motion", () => {
    const target = canvas(), engine = new KineticEngine(target, false, true, 2, true);
    intersect([{ isIntersecting: true }]);
    engine.set({ orbit: [100, -100], inspecting: true });
    expect(target.dataset.orbit).toBe("1.250,-0.750");
    expect(frame).toBeUndefined();
    engine.set({ orbit: [NaN, Infinity] });
    expect(target.dataset.orbit).toBe("0.000,0.000");
    engine.destroy();
  });
  it("renders the continuous ribbon in one draw and exposes the authored unfolded pose", () => {
    const target = canvas(), engine = new KineticEngine(target, true, true, 2, true);
    expect(target.dataset.engine).toBe("webgl");
    expect(gl.drawArrays).toHaveBeenCalledTimes(1);
    expect(gl.vertexAttribPointer).toHaveBeenCalledWith(0, 3, gl.FLOAT, false, 52, 0);
    intersect([{ isIntersecting: true }]);
    engine.set({ progress: .75 });
    expect(target.dataset.unfold).toBe("0.750");
    engine.set({ progress: 9 });
    expect(target.dataset.unfold).toBe("1.000");
    engine.set({ progress: NaN });
    expect(target.dataset.unfold).toBe("0.000");
    expect(frame).toBeUndefined();
    engine.destroy();
  });
  it("keeps the ribbon controls available after context loss and cleans resources once", () => {
    const target = canvas(), engine = new KineticEngine(target, false, false, 2, true);
    intersect([{ isIntersecting: true }]);
    target.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    engine.set({ progress: 1, exploded: true });
    expect(target.dataset.engine).toBe("fallback");
    expect(frame).toBeUndefined();
    engine.destroy();
    expect(gl.deleteBuffer).toHaveBeenCalledTimes(1);
    expect(gl.deleteProgram).toHaveBeenCalledTimes(1);
  });
  it("does not draw offscreen ribbon changes and applies them on re-entry", () => {
    const target = canvas(), engine = new KineticEngine(target, false, true, 2, true);
    intersect([{ isIntersecting: true }]);
    intersect([{ isIntersecting: false }]);
    const draws = gl.drawArrays.mock.calls.length;
    engine.set({ progress: .9 }); resize();
    expect(gl.drawArrays).toHaveBeenCalledTimes(draws);
    expect(frame).toBeUndefined();
    intersect([{ isIntersecting: true }]);
    expect(target.dataset.unfold).toBe("0.900");
    engine.destroy();
  });
  it("caps density and starts animation only when actually visible", () => {
    const target = canvas(), engine = new KineticEngine(target);
    expect(target.dataset.engine).toBe("webgl");
    expect([target.width, target.height]).toEqual([480, 384]);
    expect(frame).toBeUndefined();
    expect(gl.drawArrays).toHaveBeenCalledTimes(5);
    intersect([{ isIntersecting: true }]);
    expect(frame).toBeTypeOf("function");
    intersect([{ isIntersecting: false }]);
    expect(frame).toBeUndefined();
    engine.destroy();
  });
  it("freezes ambient frames for pause, reduced motion, and hidden tabs", () => {
    const target = canvas(), engine = new KineticEngine(target);
    intersect([{ isIntersecting: true }]);
    engine.set({ paused: true, exploded: true });
    expect(frame).toBeUndefined();
    engine.set({ paused: false });
    expect(frame).toBeTypeOf("function");
    reduced = true; motion();
    expect(frame).toBeUndefined();
    reduced = false; motion();
    expect(frame).toBeTypeOf("function");
    hidden = true; document.dispatchEvent(new Event("visibilitychange"));
    expect(frame).toBeUndefined();
    hidden = false; document.dispatchEvent(new Event("visibilitychange"));
    expect(frame).toBeTypeOf("function");
    engine.destroy();
  });
  it("uses a static fallback if GPU creation or shader compilation fails", () => {
    gl.getShaderParameter.mockReturnValue(false);
    const target = canvas(), engine = new KineticEngine(target);
    expect(target.dataset.engine).toBe("fallback");
    expect(gl.deleteShader).toHaveBeenCalledTimes(1);
    expect(gl.deleteProgram).toHaveBeenCalledTimes(1);
    expect(frame).toBeUndefined();
    engine.destroy();
    expect(gl.deleteProgram).toHaveBeenCalledTimes(1);
  });
  it("releases GPU resources and observers on context loss, without retry loops", () => {
    const target = canvas(), engine = new KineticEngine(target);
    intersect([{ isIntersecting: true }]);
    target.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(target.dataset.engine).toBe("fallback");
    expect(frame).toBeUndefined();
    expect(disconnect).toHaveBeenCalledTimes(2);
    expect(gl.deleteBuffer).toHaveBeenCalledTimes(1);
    expect(gl.deleteProgram).toHaveBeenCalledTimes(1);
    engine.set({ selected: 4 }); resize();
    expect(frame).toBeUndefined();
    engine.destroy();
    expect(gl.deleteBuffer).toHaveBeenCalledTimes(1);
  });
  it("does not respond to pointer or settings changes after destruction", () => {
    const target = canvas(), engine = new KineticEngine(target);
    engine.destroy();
    const draws = gl.drawArrays.mock.calls.length;
    target.dispatchEvent(new Event("pointermove"));
    document.dispatchEvent(new Event("visibilitychange"));
    engine.set({ selected: 3, paused: false });
    expect(gl.drawArrays).toHaveBeenCalledTimes(draws);
    expect(frame).toBeUndefined();
    expect(motionRemove).toHaveBeenCalled();
  });
});

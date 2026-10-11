import { createSlab, multiply, perspective, transform } from "./geometry";
import { createRibbon } from "../workshop/ribbon";

const vertexSource = `
attribute vec3 position; attribute vec3 normal;
uniform mat4 model; uniform mat4 projection;
varying vec3 worldPosition; varying vec3 worldNormal;
void main() { vec4 world=model*vec4(position,1.); worldPosition=world.xyz; worldNormal=mat3(model)*normal; gl_Position=projection*world; }
`;
const fragmentSource = `
precision mediump float;
uniform vec3 color; uniform float accent;
varying vec3 worldPosition; varying vec3 worldNormal;
void main() {
  vec3 n=normalize(worldNormal); vec3 light=normalize(vec3(-3.,5.,4.)); vec3 view=normalize(-worldPosition);
  float diffuse=max(dot(n,light),0.); float hemi=.3+.23*max(n.y,0.);
  float spec=pow(max(dot(n,normalize(light+view)),0.),48.);
  float rim=pow(1.-abs(dot(n,view)),3.);
  vec3 result=color*(hemi+diffuse*.68)+vec3(.85,.9,.95)*spec*.72+vec3(.55,.61,.64)*rim*.2;
  result+=accent*vec3(.12,.016,.004); gl_FragColor=vec4(result,1.);
}
`;

const ribbonVertex = `
attribute vec3 position; attribute vec3 normal; attribute vec3 openPosition; attribute vec3 openNormal; attribute float section;
uniform mat4 model; uniform mat4 projection; uniform float unfold;
varying vec3 worldPosition; varying vec3 worldNormal; varying float segment;
void main() { vec4 world=model*vec4(mix(position,openPosition,unfold),1.); worldPosition=world.xyz; worldNormal=mat3(model)*mix(normal,openNormal,unfold); segment=section; gl_Position=projection*world; }
`;
const ribbonFragment = `
precision mediump float;
uniform float selection; uniform float inspection;
varying vec3 worldPosition; varying vec3 worldNormal; varying float segment;
void main() {
  vec3 n=normalize(worldNormal); if(!gl_FrontFacing) n=-n;
  vec3 light=normalize(vec3(-3.,5.,6.)); vec3 view=normalize(-worldPosition);
  float active=1.-step(.4,abs(segment-selection));
  vec3 orange=vec3(.97,.23,.055); vec3 graphite=vec3(.23,.28,.27);
  vec3 color=mix(orange,mix(graphite,orange,active),inspection);
  float diffuse=max(dot(n,light),0.); float spec=pow(max(dot(n,normalize(light+view)),0.),64.);
  vec3 result=color*(.43+diffuse*.7)+vec3(1.,.85,.65)*spec*.65;
  result+=vec3(.22,.16,.09)*pow(1.-abs(dot(n,view)),3.);
  gl_FragColor=vec4(result,1.);
}
`;

export class KineticEngine {
  private gl: WebGLRenderingContext | null = null;
  private program?: WebGLProgram;
  private buffer?: WebGLBuffer;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private vertices = 0;
  private pointer = [0, 0];
  private current = [0, 0];
  private gap = 0;
  private frame = 0;
  private visible = false;
  private destroyed = false;
  private resize?: ResizeObserver;
  private intersection?: IntersectionObserver;
  private reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  private selected: number;
  private paused: boolean;
  private exploded = false;
  private progress = 0;
  private unfold = 0;
  private orbit = [0, 0];
  private inspecting = false;

  constructor(private canvas: HTMLCanvasElement, private compact = false, paused = false, selected = 2, private ribbon = false) {
    this.paused = paused; this.selected = selected;
    try {
      const gl = canvas.getContext("webgl", { alpha: true, antialias: true, premultipliedAlpha: false });
      this.gl = gl;
      if (!gl) throw new Error("WebGL unavailable");
      const program = gl.createProgram();
      if (!program) throw new Error("WebGL program unavailable");
      this.program = program;
      for (const [type, source] of [[gl.VERTEX_SHADER, ribbon ? ribbonVertex : vertexSource], [gl.FRAGMENT_SHADER, ribbon ? ribbonFragment : fragmentSource]] as const) {
        const shader = gl.createShader(type);
        if (!shader) throw new Error("WebGL shader unavailable");
        try {
          gl.shaderSource(shader, source); gl.compileShader(shader);
          if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error("WebGL compilation unavailable");
          gl.attachShader(program, shader);
        } finally { gl.deleteShader(shader); }
      }
      gl.bindAttribLocation(program, 0, "position"); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error("WebGL linking unavailable");
      gl.useProgram(program);
      const mesh = ribbon ? createRibbon() : createSlab(); this.vertices = mesh.length / (ribbon ? 13 : 6);
      const buffer = gl.createBuffer();
      if (!buffer) throw new Error("WebGL buffer unavailable");
      this.buffer = buffer;
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, mesh, gl.STATIC_DRAW);
      const stride = ribbon ? 52 : 24;
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, stride, 0);
      const normal = gl.getAttribLocation(program, "normal");
      if (normal < 0) throw new Error("WebGL attribute unavailable");
      gl.enableVertexAttribArray(normal); gl.vertexAttribPointer(normal, 3, gl.FLOAT, false, stride, 12);
      if (ribbon) for (const [name, size, offset] of [["openPosition", 3, 24], ["openNormal", 3, 36], ["section", 1, 48]] as const) {
        const attribute = gl.getAttribLocation(program, name);
        if (attribute < 0) throw new Error("WebGL ribbon attribute unavailable");
        gl.enableVertexAttribArray(attribute); gl.vertexAttribPointer(attribute, size, gl.FLOAT, false, stride, offset);
      }
      this.uniforms = Object.fromEntries(["model", "projection", "color", "accent", "unfold", "selection", "inspection"].map((name) => [name, gl.getUniformLocation(program, name)]));
      gl.enable(gl.DEPTH_TEST); gl.clearColor(0, 0, 0, 0);
      canvas.dataset.engine = "webgl";
      canvas.addEventListener("pointermove", this.onPointer);
      canvas.addEventListener("pointerleave", this.onLeave);
      canvas.addEventListener("webglcontextlost", this.onLoss);
      document.addEventListener("visibilitychange", this.onVisibility);
      this.reduced.addEventListener("change", this.onMotion);
      this.resize = new ResizeObserver(() => { this.draw(performance.now()); this.wake(); });
      this.resize.observe(canvas);
      this.intersection = new IntersectionObserver((entries) => {
        this.visible = entries.some((entry) => entry.isIntersecting);
        if (this.visible) { this.draw(performance.now()); this.wake(); } else this.stop();
      });
      this.intersection.observe(canvas); this.draw(performance.now());
      canvas.dataset.ready = "true";
    } catch { this.fallback(); }
  }
  private onPointer = (event: PointerEvent) => {
    if (this.reduced.matches || this.paused) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    this.pointer = [(event.clientX - rect.left) / rect.width - .5, (event.clientY - rect.top) / rect.height - .5]; this.wake();
  };
  private onLeave = () => { this.pointer = [0, 0]; this.wake(); };
  private onVisibility = () => { if (document.hidden) this.stop(); else { this.draw(performance.now()); this.wake(); } };
  private onMotion = () => { this.stop(); this.gap = this.exploded ? .27 : 0; this.draw(performance.now()); this.wake(); };
  private onLoss = (event: Event) => { event.preventDefault(); this.fallback(); };
  private release() {
    this.stop(); this.resize?.disconnect(); this.intersection?.disconnect();
    this.canvas.removeEventListener("pointermove", this.onPointer);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("webglcontextlost", this.onLoss);
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.reduced.removeEventListener("change", this.onMotion);
    if (this.buffer) this.gl?.deleteBuffer(this.buffer);
    if (this.program) this.gl?.deleteProgram(this.program);
    this.buffer = undefined; this.program = undefined;
  }
  private fallback() { this.release(); this.canvas.dataset.engine = "fallback"; this.canvas.dataset.ready = "true"; }
  set({ paused = this.paused, selected = this.selected, exploded = this.exploded, progress = this.progress, orbit = this.orbit, inspecting = this.inspecting }: { paused?: boolean; selected?: number; exploded?: boolean; progress?: number; orbit?: readonly number[]; inspecting?: boolean } = {}) {
    this.paused = paused; this.selected = Math.max(0, Math.min(4, selected)); this.exploded = exploded;
    this.progress = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
    this.orbit = [1.25, .75].map((limit, i) => Number.isFinite(orbit[i]) ? Math.max(-limit, Math.min(limit, orbit[i])) : 0);
    this.inspecting = inspecting;
    if (this.paused || this.reduced.matches) { this.stop(); this.gap = exploded ? .27 : 0; }
    this.draw(performance.now()); this.wake();
  }
  private stop() { cancelAnimationFrame(this.frame); this.frame = 0; }
  private wake() {
    if (this.destroyed || this.frame || !this.visible || document.hidden || this.canvas.dataset.engine !== "webgl" || this.paused || this.reduced.matches) return;
    this.frame = requestAnimationFrame((time) => { this.frame = 0; this.draw(time); this.wake(); });
  }
  private draw(time: number) {
    if (this.destroyed || !this.gl || this.canvas.dataset.engine !== "webgl" || document.hidden || !this.visible && Number(this.canvas.dataset.frames) > 0) return;
    const canvas = this.canvas, gl = this.gl;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.6);
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio)), height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; gl.viewport(0, 0, width, height); }
    const still = this.paused || this.reduced.matches;
    this.current = this.current.map((value, i) => still ? 0 : value + (this.pointer[i] - value) * .075);
    this.gap += ((this.exploded ? .27 : 0) - this.gap) * .085;
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.useProgram(this.program!);
    gl.uniformMatrix4fv(this.uniforms.projection, false, perspective(width / height));
    const float = still ? 0 : Math.sin(time * .00065) * .045;
    const camera = (this.compact ? -7.5 : -7.1) * Math.max(1, .8 / (width / height)) * (1 + this.gap * 1.1);
    const root = transform(0, -.04, camera, .38 + this.current[1] * .25, -.58 + this.current[0] * .45, 1.13);
    if (this.ribbon) {
      const target = this.exploded ? 1 : this.progress;
      this.unfold = still ? target : this.unfold + (target - this.unfold) * .09;
      const aspect = width / height;
      const distance = -8.5 * Math.max(1, .85 / aspect);
      const turn = still || this.compact ? 0 : Math.sin(time * .00025) * .18;
      gl.uniformMatrix4fv(this.uniforms.model, false, transform(0, float, distance, -.12 + this.current[1] * .3 + this.orbit[1] + this.unfold * .18, -.23 + this.current[0] * .5 + this.orbit[0] + turn + this.unfold * .24, this.compact ? .92 : 1.2));
      gl.uniform1f(this.uniforms.unfold, this.unfold);
      gl.uniform1f(this.uniforms.selection, this.selected);
      gl.uniform1f(this.uniforms.inspection, this.compact || this.inspecting ? 1 : 0);
      gl.drawArrays(gl.TRIANGLES, 0, this.vertices);
      canvas.dataset.unfold = this.unfold.toFixed(3);
      canvas.dataset.orbit = this.orbit.map((value) => value.toFixed(3)).join(",");
      canvas.dataset.frames = String((Number(canvas.dataset.frames) || 0) + 1);
      if (still) gl.flush();
      return;
    }
    for (let i = 0; i < 5; i++) {
      const model = multiply(root, transform(0, (i - 2) * (.37 + this.gap) + float, 0, 0, (i - 2) * .045));
      gl.uniformMatrix4fv(this.uniforms.model, false, model);
      const active = i === 4 - this.selected;
      gl.uniform3fv(this.uniforms.color, active ? [.97, .23, .07] : i === 4 ? [.17, .205, .21] : [.31, .35, .36]);
      gl.uniform1f(this.uniforms.accent, active ? 1 : 0); gl.drawArrays(gl.TRIANGLES, 0, this.vertices);
    }
    canvas.dataset.frames = String((Number(canvas.dataset.frames) || 0) + 1);
    if (still) gl.flush();
  }
  destroy() { this.destroyed = true; this.release(); }
}

import { createSlab, multiply, perspective, transform } from './geometry.js';

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
  result+=accent*vec3(.12,.016,.004);
  gl_FragColor=vec4(result,1.);
}
`;

export class KineticEngine {
  constructor(canvas, { compact=false, paused=false, selected=2 }={}) {
    this.canvas=canvas; this.compact=compact; this.paused=paused; this.selected=selected;
    this.pointer=[0,0]; this.current=[0,0]; this.exploded=false; this.gap=0;
    this.frame=0; this.visible=true; this.destroyed=false;
    this.reduced=matchMedia('(prefers-reduced-motion: reduce)');
    this.gl=canvas.getContext('webgl',{alpha:true,antialias:true,premultipliedAlpha:false});
    if (!this.gl) { this.fallback(); return; }
    const gl=this.gl;
    try {
      const program=gl.createProgram(); this.program=program;
      for(const [type,source] of [[gl.VERTEX_SHADER,vertexSource],[gl.FRAGMENT_SHADER,fragmentSource]]) {
        const shader=gl.createShader(type); gl.shaderSource(shader,source); gl.compileShader(shader); gl.attachShader(program,shader); gl.deleteShader(shader);
      }
      gl.bindAttribLocation(program,0,'position'); gl.linkProgram(program);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS)) throw new Error('Engine shader unavailable');
      gl.useProgram(program);
      const mesh=createSlab(); this.vertices=mesh.length/6;
      this.buffer=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer); gl.bufferData(gl.ARRAY_BUFFER,mesh,gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);
      const normal=gl.getAttribLocation(program,'normal'); gl.enableVertexAttribArray(normal); gl.vertexAttribPointer(normal,3,gl.FLOAT,false,24,12);
      this.uniforms=Object.fromEntries(['model','projection','color','accent'].map(name=>[name,gl.getUniformLocation(program,name)]));
      gl.enable(gl.DEPTH_TEST); gl.clearColor(0,0,0,0);
      canvas.dataset.engine='webgl';
      this.onPointer=event=>{
        if(this.reduced.matches || this.paused) return;
        const r=canvas.getBoundingClientRect(); this.pointer=[(event.clientX-r.left)/r.width-.5,(event.clientY-r.top)/r.height-.5]; this.wake();
      };
      this.onLeave=()=>{this.pointer=[0,0];this.wake();};
      this.onVisibility=()=>{if(document.hidden) this.stop(); else this.wake();};
      this.onMotion=()=>{this.stop();this.draw(performance.now());this.wake();};
      this.onLoss=event=>{event.preventDefault();this.stop();this.fallback();};
      this.onRestore=()=>{canvas.dataset.engine='fallback';};
      canvas.addEventListener('pointermove',this.onPointer); canvas.addEventListener('pointerleave',this.onLeave);
      canvas.addEventListener('webglcontextlost',this.onLoss); canvas.addEventListener('webglcontextrestored',this.onRestore);
      document.addEventListener('visibilitychange',this.onVisibility); this.reduced.addEventListener('change',this.onMotion);
      this.resize=new ResizeObserver(()=>{this.draw(performance.now());this.wake();}); this.resize.observe(canvas);
      this.intersection=new IntersectionObserver(entries=>{this.visible=entries[0].isIntersecting;if(this.visible)this.wake();else this.stop();}); this.intersection.observe(canvas);
      this.draw(performance.now()); this.wake();
    } catch { this.fallback(); }
  }
  fallback() { this.canvas.dataset.engine='fallback'; this.canvas.closest('.engine-stage')?.classList.add('engine-fallback'); }
  set({paused=this.paused,selected=this.selected,exploded=this.exploded}={}) {
    this.paused=paused;this.selected=selected;this.exploded=exploded;
    if(this.paused || this.reduced.matches) this.stop();
    if(this.reduced.matches || this.paused) this.gap=exploded ? .27 : 0;
    this.draw(performance.now());this.wake();
  }
  stop() { cancelAnimationFrame(this.frame); this.frame=0; }
  wake() {
    if(this.destroyed || this.frame || !this.visible || document.hidden || this.canvas.dataset.engine!=='webgl') return;
    if(this.paused || this.reduced.matches) return;
    this.frame=requestAnimationFrame(time=>{this.frame=0;this.draw(time);this.wake();});
  }
  draw(time) {
    if(this.destroyed || !this.gl || this.canvas.dataset.engine!=='webgl') return;
    const canvas=this.canvas,gl=this.gl;
    const ratio=Math.min(devicePixelRatio || 1,1.6);
    const width=Math.max(1,Math.round(canvas.clientWidth*ratio)),height=Math.max(1,Math.round(canvas.clientHeight*ratio));
    if(canvas.width!==width || canvas.height!==height) { canvas.width=width;canvas.height=height;gl.viewport(0,0,width,height); }
    const still=this.paused || this.reduced.matches;
    this.current=this.current.map((v,i)=>still?0:v+(this.pointer[i]-v)*.075);
    this.gap+=((this.exploded ? .27 : 0)-this.gap)*.085;
    gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);
    gl.uniformMatrix4fv(this.uniforms.projection,false,perspective(width/height));
    const float=still?0:Math.sin(time*.00065)*.045;
    const camera=(this.compact ? -7.5 : -7.1)*Math.max(1,.8/(width/height))*(1+this.gap*1.1);
    const root=transform(0,this.compact?-.02:-.04,camera,.38+this.current[1]*.25,-.58+this.current[0]*.45,1.13);
    for(let i=0;i<5;i++) {
      const model=multiply(root,transform(0,(i-2)*(.37+this.gap)+float,0,0,(i-2)*.045));
      gl.uniformMatrix4fv(this.uniforms.model,false,model);
      const active=i===4-this.selected;
      gl.uniform3fv(this.uniforms.color,active?[.97,.23,.07]:i===4?[.17,.205,.21]:[.31,.35,.36]);
      gl.uniform1f(this.uniforms.accent,active?1:0);gl.drawArrays(gl.TRIANGLES,0,this.vertices);
    }
    canvas.dataset.frames=String((Number(canvas.dataset.frames)||0)+1);
    if(still) gl.flush();
  }
  destroy() {
    this.destroyed=true;this.stop(); this.resize?.disconnect();this.intersection?.disconnect();
    this.canvas.removeEventListener('pointermove',this.onPointer);this.canvas.removeEventListener('pointerleave',this.onLeave);
    this.canvas.removeEventListener('webglcontextlost',this.onLoss);this.canvas.removeEventListener('webglcontextrestored',this.onRestore);
    document.removeEventListener('visibilitychange',this.onVisibility);this.reduced.removeEventListener('change',this.onMotion);
    if(this.gl) {if(this.buffer)this.gl.deleteBuffer(this.buffer);if(this.program)this.gl.deleteProgram(this.program);}
  }
}

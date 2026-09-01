import { Crosshair, Maximize2, Minus, Plus, Route } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { ExecutionMeshModel, MeshEdge, MeshNode } from "./model";

export interface ExecutionMeshProps {
  focusRequest?: number;
  highlightTaskId?: string;
  model: ExecutionMeshModel;
  onSelectEdge?: (edge: MeshEdge) => void;
  onSelectNode: (node: MeshNode) => void;
  selectedId?: string;
}

interface ViewTransform {
  scale: number;
  x: number;
  y: number;
}

interface DragState {
  originX: number;
  originY: number;
  pointerId: number;
  startX: number;
  startY: number;
}

export function ExecutionMesh({ focusRequest = 0, highlightTaskId, model, onSelectEdge, onSelectNode, selectedId }: ExecutionMeshProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, SVGGElement>());
  const drag = useRef<DragState | undefined>(undefined);
  const [view, setView] = useState<ViewTransform>({ scale: 1, x: 0, y: 0 });
  const [criticalOnly, setCriticalOnly] = useState(false);
  const selected = selectedId ?? model.activeNodeId ?? model.nodes[0]?.id;
  const modelHeight = model.height;
  const modelWidth = model.width;

  useEffect(() => {
    setView(fitView(modelWidth, modelHeight, viewport.current?.getBoundingClientRect()));
  }, [modelHeight, modelWidth]);

  useEffect(() => {
    if (focusRequest > 0 && model.activeNodeId) nodeRefs.current.get(model.activeNodeId)?.focus();
  }, [focusRequest, model.activeNodeId]);

  function fitToRun() {
    setView(fitView(model.width, model.height, viewport.current?.getBoundingClientRect()));
  }

  function focusActive() {
    if (!model.activeNodeId) return;
    nodeRefs.current.get(model.activeNodeId)?.focus();
    const node = model.nodes.find((item) => item.id === model.activeNodeId);
    if (node) onSelectNode(node);
  }

  function zoom(delta: number) {
    setView((current) => ({ ...current, scale: Math.min(1.6, Math.max(0.42, current.scale + delta)) }));
  }

  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.target !== event.currentTarget) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      originX: view.x,
      originY: view.y,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY
    };
  }

  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    setView((current) => ({
      ...current,
      x: drag.current!.originX + event.clientX - drag.current!.startX,
      y: drag.current!.originY + event.clientY - drag.current!.startY
    }));
  }

  function pointerUp(event: PointerEvent<SVGSVGElement>) {
    if (drag.current?.pointerId === event.pointerId) drag.current = undefined;
  }

  function keyDown(event: KeyboardEvent<SVGGElement>, node: MeshNode) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectNode(node);
      return;
    }
    const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1
      : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (!direction) return;
    event.preventDefault();
    const index = model.nodes.findIndex((item) => item.id === node.id);
    const next = model.nodes[(index + direction + model.nodes.length) % model.nodes.length];
    nodeRefs.current.get(next.id)?.focus();
    onSelectNode(next);
  }

  if (!model.nodes.length) {
    return <div className="mesh-empty"><Route size={20} /><strong>No persisted topology</strong><span>The execution mesh appears when AgentBus persists a run graph.</span></div>;
  }

  return (
    <section className="execution-mesh" aria-label="Execution mesh">
      <header className="mesh-toolbar">
        <div><span className="observatory-label">Execution mesh</span><strong>{model.nodes.length} nodes / {model.edges.length} transitions</strong></div>
        <div className="mesh-controls" aria-label="Execution mesh controls">
          <button type="button" onClick={() => setCriticalOnly((value) => !value)} aria-pressed={criticalOnly} title="Highlight critical path"><Route size={14} /><span>Critical</span></button>
          <button type="button" onClick={focusActive} disabled={!model.activeNodeId} title="Focus active execution"><Crosshair size={14} /><span>Active</span></button>
          {model.nodes.length > 10 && <><button type="button" onClick={() => zoom(-0.12)} aria-label="Zoom out"><Minus size={14} /></button><button type="button" onClick={() => zoom(0.12)} aria-label="Zoom in"><Plus size={14} /></button></>}
          <button type="button" onClick={fitToRun} title="Fit entire run"><Maximize2 size={14} /><span>Fit</span></button>
        </div>
      </header>
      <div className="mesh-viewport" ref={viewport} data-node-count={model.nodes.length}>
        <svg
          aria-label="Persisted execution topology. Use arrow keys to move between nodes."
          onPointerDown={pointerDown}
          onPointerMove={pointerMove}
          onPointerUp={pointerUp}
          role="group"
          viewBox="0 0 1000 480"
        >
          <defs>
            <marker id="mesh-arrow" markerHeight="7" markerWidth="7" orient="auto" refX="6" refY="3.5"><path d="M0,0 L7,3.5 L0,7 z" /></marker>
            <pattern id="mesh-grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" /></pattern>
          </defs>
          <rect className="mesh-grid" width="100%" height="100%" fill="url(#mesh-grid)" />
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
            {model.edges.map((edge) => {
              const source = model.nodes.find((node) => node.id === edge.source)!;
              const target = model.nodes.find((node) => node.id === edge.target)!;
              const path = edgePath(source, target);
              const subdued = criticalOnly && !edge.critical;
              return <g className={`mesh-edge tone-${edge.tone} ${edge.critical ? "is-critical" : ""} ${subdued ? "is-subdued" : ""}`} key={edge.id}>
                <path className="mesh-edge-line" d={path} markerEnd="url(#mesh-arrow)" />
                <path className="mesh-edge-hit" d={path} onClick={() => onSelectEdge?.(edge)}><title>{edge.detail}</title></path>
              </g>;
            })}
            {model.nodes.map((node) => {
              const isSelected = node.id === selected;
              const isActive = node.id === model.activeNodeId;
              return (
                <g
                  aria-current={isActive ? "step" : undefined}
                  aria-label={`${node.label}, ${node.rawStatus}. ${node.detail}`}
                  className={`mesh-node kind-${node.kind} tone-${node.tone} ${isSelected ? "is-selected" : ""} ${isActive ? "is-active" : ""} ${node.dimmed ? "is-dimmed" : ""} ${highlightTaskId && node.taskId === highlightTaskId ? "is-related" : ""} ${highlightTaskId && node.taskId && node.taskId !== highlightTaskId ? "is-unrelated" : ""}`}
                  data-node-id={node.id}
                  key={node.id}
                  onClick={() => onSelectNode(node)}
                  onKeyDown={(event) => keyDown(event, node)}
                  ref={(element) => { if (element) nodeRefs.current.set(node.id, element); else nodeRefs.current.delete(node.id); }}
                  role="button"
                  tabIndex={isSelected ? 0 : -1}
                  transform={`translate(${node.x} ${node.y})`}
                >
                  <rect className="mesh-node-body" width={node.width} height={node.height} rx="4" />
                  <path className="mesh-node-notch" d={`M0 16 V4 Q0 0 4 0 H28 L35 7 H${node.width}`} />
                  <text className="mesh-node-kind" x="12" y="19">{kindCode(node.kind)}</text>
                  <text className="mesh-node-label" x="12" y="39">{clip(node.label, 21)}</text>
                  <text className="mesh-node-detail" x="12" y="54">{clip(node.detail, 25)}</text>
                  <circle className="mesh-node-state" cx={node.width - 12} cy="17" r="4" />
                  {node.kind === "approval" && <path className="mesh-gate-mark" d={`M${node.width - 31} 29v20m8-20v20`} />}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <ol className="sr-only" aria-label="Execution mesh textual equivalent">
        {model.nodes.map((node) => <li key={node.id}>{node.label}: {node.rawStatus}. {node.detail}</li>)}
      </ol>
    </section>
  );
}

function edgePath(source: MeshNode, target: MeshNode): string {
  const sourceX = source.x + source.width;
  const sourceY = source.y + source.height / 2;
  const targetX = target.x;
  const targetY = target.y + target.height / 2;
  const middle = sourceX + (targetX - sourceX) / 2;
  return `M${sourceX} ${sourceY} C${middle} ${sourceY}, ${middle} ${targetY}, ${targetX} ${targetY}`;
}

function kindCode(kind: MeshNode["kind"]): string {
  return ({ planner: "PLN", coder: "COD", tool: "TOL", approval: "GATE", verifier: "VER", reviewer: "REV", retry: "RTY", evidence: "EVD" })[kind];
}

function clip(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}...`;
}

function fitView(modelWidth: number, modelHeight: number, bounds?: DOMRect): ViewTransform {
  const availableWidth = Math.max(640, bounds?.width ?? 960) - 48;
  const availableHeight = Math.max(300, bounds?.height ?? 460) - 48;
  const scale = Math.min(1, availableWidth / modelWidth, availableHeight / modelHeight);
  return {
    scale,
    x: Math.max(24, (availableWidth - modelWidth * scale) / 2 + 24),
    y: Math.max(20, (availableHeight - modelHeight * scale) / 2 + 24)
  };
}

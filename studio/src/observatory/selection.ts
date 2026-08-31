import type { EventEnvelope } from "../api/types";
import type { MeshEdge, MeshNode } from "./model";

export type InspectorView = "run" | "source" | "verifier" | "review" | "evidence" | "runtime";

export type ObservatorySelection =
  | { kind: "node"; node: MeshNode }
  | { kind: "edge"; edge: MeshEdge }
  | { kind: "event"; event: EventEnvelope }
  | { kind: "file"; path: string }
  | { kind: "view"; view: InspectorView };

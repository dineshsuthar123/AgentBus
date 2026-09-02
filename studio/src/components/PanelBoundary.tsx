import { Component, type ReactNode } from "react";
import { ErrorPanel } from "./Primitives";

interface PanelBoundaryProps {
  children: ReactNode;
  name: string;
  safetyCritical?: boolean;
}

interface PanelBoundaryState {
  failed: boolean;
}

export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  public state: PanelBoundaryState = { failed: false };

  public static getDerivedStateFromError(): PanelBoundaryState {
    return { failed: true };
  }

  public componentDidCatch(): void {
    // The boundary deliberately avoids logging payloads or tool arguments.
  }

  public render() {
    if (!this.state.failed) return this.props.children;
    const message = this.props.safetyCritical
      ? "This approval surface could not render. No decision was sent and execution remains paused. Refresh authoritative run state before acting."
      : `${this.props.name} could not render. The rest of the execution observatory remains available.`;
    return <div className="panel-boundary-fallback"><ErrorPanel title={`${this.props.name} unavailable`} message={message} /><button className="button button-secondary" type="button" onClick={() => this.setState({ failed: false })}>Retry panel</button></div>;
  }
}

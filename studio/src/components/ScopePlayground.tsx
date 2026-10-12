import { useState } from "react";
import { ArrowLeftRight, CornerDownRight, Shield } from "lucide-react";

export function ScopePlayground({ scope, protectedPath }: { scope: string; protectedPath: string }) {
  const [outside, setOutside] = useState(false);
  return <div className="scope-playground" data-outside={outside}>
    <div className="scope-territory" aria-hidden="true"><span className="territory-label">EXAMPLE WRITE AREA</span><span className="territory-edge" /><span className="territory-request"><CornerDownRight size={18} />{outside ? "Protected target" : "Write request"}</span><Shield className="territory-stop" size={25} /></div>
    <button type="button" aria-pressed={outside} onClick={() => setOutside((value) => !value)}><ArrowLeftRight size={15} />{outside ? "Return to example write area" : "Try the protected boundary"}</button>
    <p role="status">{outside ? `Outside the example scope: ${protectedPath}. The illustration stops at the boundary; it cannot authorize a write.` : `Inside the example scope: ${scope}, not authorized. A real write still requires the actual capability and any exact approval.`}</p>
  </div>;
}

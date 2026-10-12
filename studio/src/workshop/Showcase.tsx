import { useEffect, useState } from "react";
import { ArrowUpRight, Copy, Download, Moon, Sun } from "lucide-react";
import { WorkshopJourney } from "../components/WorkshopJourney";

const repository = "https://github.com/dineshsuthar123/AgentBus";

export function Showcase() {
  const [dark, setDark] = useState(false);
  const [platform, setPlatform] = useState<"windows" | "unix">("windows");
  const [copyStatus, setCopyStatus] = useState("");
  const command = platform === "windows"
    ? 'py -m venv .venv\n.venv\\Scripts\\python.exe -m pip install "$HOME\\Downloads\\syndra-0.6.0b1-py3-none-any.whl[ide]"\n.venv\\Scripts\\syndra.exe studio --workspace .'
    : 'python3 -m venv .venv\n.venv/bin/python -m pip install "$HOME/Downloads/syndra-0.6.0b1-py3-none-any.whl[ide]"\n.venv/bin/syndra studio --workspace .';
  useEffect(() => { document.documentElement.dataset.theme = dark ? "dark" : "light"; }, [dark]);
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(command);
      setCopyStatus("Installation commands copied.");
    } catch { setCopyStatus("Clipboard unavailable. Select the command text to copy it."); }
  }
  return <main className="connection-workshop public-showcase">
    <a className="skip-link" href="#local-install">Skip to local installation</a>
    <div className="showcase-notice"><span>Public interactive preview. Execution stays on your device.</span><button type="button" aria-label={dark ? "Use light appearance" : "Use dark appearance"} onClick={() => setDark((value) => !value)}>{dark ? <Sun size={16} /> : <Moon size={16} />}{dark ? "Light" : "Dark"}</button></div>
    <WorkshopJourney entryActionLabel="Install locally" entry={<div className="showcase-install" id="local-install">
      <p className="eyebrow">The real workshop runs locally</p>
      <h3>Your repository.<br />Your execution boundary.</h3>
      <p>This website is a local-only illustration, not a hosted executor. It cannot read your files, launch a task, connect to your daemon, or grant permissions.</p>
      <a className="button button-primary" href={`${repository}/releases`}><Download size={17} />View reviewed downloads<ArrowUpRight size={17} /></a>
      <p className="showcase-release-note">The current Syndra build is a development preview. Download availability follows release review; the website is not evidence of a published beta.</p>
      <div className="showcase-platforms" role="group" aria-label="Installation platform"><button type="button" aria-pressed={platform === "windows"} onClick={() => { setPlatform("windows"); setCopyStatus(""); }}>Windows</button><button type="button" aria-pressed={platform === "unix"} onClick={() => { setPlatform("unix"); setCopyStatus(""); }}>macOS / Linux</button></div>
      <p>Download the Syndra wheel to Downloads and use Python 3.11 or newer. Run these commands from your repository, using PowerShell on Windows:</p>
      <pre className="showcase-command" tabIndex={0} aria-label="Local installation commands"><code>{command}</code></pre>
      <button type="button" onClick={() => void copy()}><Copy size={15} />Copy installation commands</button><p className="showcase-copy-status" role="status">{copyStatus}</p>
      <p className="showcase-cost">The offline guided fixture needs no API key. Arbitrary AI tasks require a configured provider; cloud models may charge separately. This deployment does not enable them.</p>
      <a className="text-link" href={repository}>Inspect source and security guidance<ArrowUpRight size={16} /></a>
    </div>} />
  </main>;
}

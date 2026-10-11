# Local Installation And Free Public Deployment

## Separate Boundaries

The real Studio, backend and CLI ship together in the Python wheel. The VS Code
extension ships as a separate VSIX. Repository execution stays on the user's
machine behind the authenticated numeric-loopback boundary.

The public website is a separate static showcase, not a remotely hosted
executor. It imports the Living Workshop directly, without StudioProvider,
authentication, run APIs, workspace tools, or secrets. Its CSP uses
`connect-src 'none'`. It cannot connect to a visitor's local backend.

Hosting uses the existing public GitHub repository and GitHub Pages project
address, without a custom domain, paid runner, cloud VM, or database. GitHub
Pages is available for public repositories on GitHub Free. Standard hosted
runners for public repositories are free; storage and service usage limits
still apply. See [Pages availability](https://docs.github.com/en/pages/getting-started-with-github-pages)
and [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Syndra does not make a cloud provider free. Offline guided fixtures use no API
key. Live Azure and other paid models remain separately configured, optional,
and potentially chargeable. This deployment does not invoke them.

## Local Installation

Download a reviewed wheel and VSIX from the repository's Releases page. Before
installing, check their SHA-256 checksums against the release checksum file.
Until a Syndra release is published, its download availability remains pending;
the public preview is not a release announcement.

PowerShell, from a local Git workspace:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install "$HOME\Downloads\syndra-0.6.0b1-py3-none-any.whl[ide]"
code --install-extension "$HOME\Downloads\syndra-vscode.vsix"
.venv\Scripts\syndra.exe studio --workspace .
```

Installed Studio needs no Node server. Keep the foreground process running;
Ctrl+C stops its owned daemon. Do not expose it through a public bind, reverse
proxy, or tunnel. A compatible already-running daemon still needs an
authenticated extension or browser session; the CLI does not attach blindly.

The deployment on the maintainer's Windows machine uses a separate per-user
environment under `%LOCALAPPDATA%\Programs\Syndra\venv`. It does not overwrite
the source checkout's development environment. Its Scripts directory is added
to the user PATH, not the system PATH. Restart VS Code so new terminals and the
extension inherit the updated PATH.

## Public Showcase

```powershell
cd studio
npm.cmd run lint
npm.cmd test
npm.cmd run build:showcase
$env:SYNDRA_BROWSER_CHANNEL = 'chrome'
npm.cmd run test:showcase
```

The generated `studio/showcase-dist/` uses the canonical repository's project
URL; the local Studio build remains dedicated to `/studio/`. Never substitute the real Studio
bundle for the public showcase. The smoke test checks the isolated bundle,
project-relative assets, clipboard fallbacks, mobile/touch composition, light
and dark themes, accessibility, and absence of API or external requests.

Publish only the tested static artifact to the dedicated `gh-pages` branch.
Use a clean, separate deployment worktree, a normal fast-forward push, and
GitHub Pages' branch source. Do not force-push an existing website, commit
runtime files, or publish a source checkout as the site root. The public build
is also checked by the main Studio CI job.

## Release Gates

Follow `RELEASE_CHECKLIST.md` before publishing the wheel, sdist or VSIX. The
release must identify the reviewed source commit; a public demo does not waive
full tests, installed acceptance, archive audits, or credential checks. Keep
release drafts unpublished while required CI checks are pending or failing.
Do not upload to PyPI or the VS Code Marketplace without verified package and
publisher ownership; GitHub Releases can distribute all artifacts directly.

The public website runs without a backend service or hosted database. Local
state and workspace changes are not uploaded. Failed local runs may retain
their filesystem edits; deployment never automatically resets or cleans them.

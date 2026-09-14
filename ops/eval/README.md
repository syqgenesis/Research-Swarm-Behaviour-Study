# Installed evaluation tools

Installed on 12 September 2026 in the project-local `.eval-tools/` directory.

| Tool | Version |
|---|---|
| Python | 3.12.13, Apple Silicon |
| Inspect Scout | 0.5.2 |
| Inspect AI | 0.3.263 |
| NetworkX | 3.6.1 |
| OpenAI-compatible provider client | 3.13.0 |
| Gephi | 0.11.3, Apple Silicon |

The provider client is installed for later model grading; no credentials were added and no model grading has run. DeepEval, Phoenix and Promptfoo remain alternatives, not additional installations.

## Open Scout

Run from the project root:

```sh
.eval-tools/venv/bin/scout view \
  --transcripts workspaces/conference25d/scout-pilot/transcripts \
  --scans workspaces/conference25d/scout-pilot/scans \
  --host 127.0.0.1 --port 8788 --no-browser
```

Then open <http://127.0.0.1:8788>. The viewer runs locally.

## Open Gephi

Run from the project root:

```sh
.eval-tools/apps/Gephi.app/Contents/MacOS/gephi \
  --userdir "$PWD/.eval-tools/gephi-user" \
  --cachedir "$PWD/.eval-tools/gephi-cache" \
  --open "$PWD/workspaces/conference25d/codex-eval-tooling-pilot/communication.gexf"
```

The application is kept inside this project, rather than in the system Applications directory. The launcher help/version check passed; graphical graph import has not yet been reviewed.

## Checks performed

- Scout CLI version and help work.
- Dependency compatibility check passes for all 101 installed Python packages.
- Scout imported eight saved request-plus-response snapshots from eight distinct agents. Conversion preserved message counts and exact final reasoning text.
- Scout's built-in grep scanner completed eight scans with zero errors and zero model tokens. It searched for the word `the` solely to exercise installation and result storage; this is not a misconduct grader.
- The existing 52 analysis/report/statistics tests passed in the new environment with the original network guard active.
- Gephi's downloaded disk image passed its built-in checksum verification, and the installed launcher reports 0.11.3 and displays help.

`requirements.lock.txt` records exact Python package versions. `installed.json` records the primary versions and original Gephi download URL. `gephi-download.sha256` records the locally calculated download checksum; it is a reproducibility record, not an independently published checksum.

To recreate the Python environment, use Python 3.12 and install `requirements.lock.txt` into a separate environment. The installed tools are excluded from Git via `.eval-tools/`.

## Review starter

See `workspaces/conference25d/scout-pilot/README.md`. No human labels have been invented or filled in. Review the small starter before choosing the model grader and expanding to a validation dataset.

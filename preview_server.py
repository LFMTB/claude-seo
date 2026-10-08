#!/usr/bin/env python3
"""Lightweight dashboard server for the Claude SEO plugin preview.

Serves a single-page dashboard on port 3000 that showcases the project's
skills, agents, scripts, and can run built-in checks (portability, doctor).
Uses only the Python standard library — no extra dependencies required.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _load_plugin_info() -> dict:
    manifest = ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"name": "claude-seo", "version": "unknown", "description": ""}


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML-like frontmatter fields from a markdown file."""
    fm = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return fm
    block = match.group(1)
    for line in block.splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        if val in (">", "|"):
            val = ""
        fm[key] = val
    return fm


def _get_skills() -> list[dict]:
    skills = []
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            skills.append({
                "name": fm.get("name", skill_dir.name),
                "description": fm.get("description", ""),
                "invocable": fm.get("user-invocable", "false") == "true",
                "hint": fm.get("argument-hint", ""),
            })
        except OSError:
            pass
    return skills


def _get_agents() -> list[dict]:
    agents = []
    agents_dir = ROOT / "agents"
    if not agents_dir.is_dir():
        return agents
    for f in sorted(agents_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            agents.append({
                "name": fm.get("name", f.stem),
                "description": fm.get("description", ""),
                "model": fm.get("model", ""),
            })
        except OSError:
            pass
    return agents


def _get_scripts() -> list[str]:
    scripts_dir = ROOT / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(
        f.name for f in scripts_dir.glob("*.py")
        if f.name != "__init__.py"
    )


def _run_portability() -> dict:
    script = ROOT / "scripts" / "portability_check.py"
    if not script.is_file():
        return {"ok": False, "output": "portability_check.py not found"}
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        try:
            data = json.loads(output)
            return {"ok": result.returncode == 0, "data": data}
        except json.JSONDecodeError:
            return {"ok": result.returncode == 0, "output": output or result.stderr}
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def _run_doctor() -> dict:
    launcher = ROOT / "scripts" / "claude-seo"
    if not launcher.is_file():
        return {"ok": False, "output": "launcher not found"}
    try:
        result = subprocess.run(
            [str(launcher), "doctor", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip()
        try:
            return {"ok": result.returncode == 0, "data": json.loads(output)}
        except json.JSONDecodeError:
            return {"ok": result.returncode == 0, "output": output or result.stderr}
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def _get_audit() -> dict:
    audit_path = ROOT / "luxuryformen.com-audit" / "audit-data.json"
    try:
        return json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": "No audit data found. Run an audit first."}


def _get_audit_report() -> str:
    report_path = ROOT / "luxuryformen.com-audit" / "FULL-AUDIT-REPORT.md"
    try:
        return report_path.read_text(encoding="utf-8")
    except OSError:
        return "No audit report found."


def _test_count() -> str:
    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir():
        return "0"
    return str(len(list(tests_dir.glob("test_*.py"))))


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude SEO — Plugin Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text-dim: #8b949e; --accent: #58a6ff;
    --accent2: #3fb950; --warn: #d29922; --err: #f85149;
    --radius: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6;
  }
  .header {
    background: linear-gradient(135deg, #1a1f2e, #0d1117);
    border-bottom: 1px solid var(--border); padding: 2rem 2rem 1.5rem;
  }
  .header h1 { font-size: 1.75rem; font-weight: 700; }
  .header .version {
    display: inline-block; background: var(--accent); color: #000;
    font-size: .8rem; font-weight: 700; padding: 2px 8px;
    border-radius: 4px; margin-left: .5rem; vertical-align: middle;
  }
  .header p { color: var(--text-dim); margin-top: .5rem; max-width: 70ch; }
  .container { max-width: 1100px; margin: 0 auto; padding: 1.5rem 2rem 3rem; }
  .tabs {
    display: flex; gap: .25rem; border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem; flex-wrap: wrap;
  }
  .tab {
    padding: .6rem 1.2rem; cursor: pointer; border: none;
    background: none; color: var(--text-dim); font-size: .95rem;
    border-bottom: 2px solid transparent; transition: all .15s;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .panel { display: none; }
  .panel.active { display: block; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1.2rem;
  }
  .card h3 { font-size: 1rem; margin-bottom: .4rem; }
  .card h3 .badge {
    font-size: .7rem; padding: 1px 6px; border-radius: 3px;
    background: var(--accent); color: #000; font-weight: 600; margin-left: .4rem;
    vertical-align: middle;
  }
  .card p { color: var(--text-dim); font-size: .85rem; }
  .stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
  .stat {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1rem 1.5rem; text-align: center;
    flex: 1; min-width: 120px;
  }
  .stat .num { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
  .stat .label { font-size: .8rem; color: var(--text-dim); text-transform: uppercase; }
  .btn {
    background: var(--accent); color: #000; border: none; padding: .6rem 1.2rem;
    border-radius: var(--radius); font-weight: 600; cursor: pointer;
    font-size: .9rem; transition: opacity .15s;
  }
  .btn:hover { opacity: .85; }
  .btn:disabled { opacity: .4; cursor: default; }
  .btn.sec { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .output {
    background: #010409; border: 1px solid var(--border); border-radius: var(--radius);
    padding: 1rem; font-family: 'SF Mono', 'Fira Code', monospace; font-size: .82rem;
    white-space: pre-wrap; word-break: break-word; margin-top: 1rem;
    max-height: 400px; overflow-y: auto; color: var(--text-dim);
  }
  .output.ok { border-color: var(--accent2); }
  .output.fail { border-color: var(--err); }
  .status-row { display: flex; align-items: center; gap: .5rem; margin: .3rem 0; font-size: .9rem; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .dot.green { background: var(--accent2); } .dot.red { background: var(--err); }
  .dot.yellow { background: var(--warn); }
  .script-list { columns: 2; column-gap: 2rem; }
  .script-list li {
    list-style: none; font-family: monospace; font-size: .82rem;
    color: var(--text-dim); padding: 2px 0; break-inside: avoid;
  }
  .script-list li::before { content: "\2023  "; color: var(--accent); }
  .check-section { margin-top: 1.5rem; }
  .actions { display: flex; gap: .75rem; flex-wrap: wrap; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="header">
  <h1>Claude SEO<span class="version" id="ver">v?</span></h1>
  <p id="desc">Loading...</p>
</div>
<div class="container">
  <div class="stats" id="stats"></div>

  <div class="tabs">
    <button class="tab active" data-tab="overview">Overview</button>
    <button class="tab" data-tab="skills">Skills</button>
    <button class="tab" data-tab="agents">Agents</button>
    <button class="tab" data-tab="scripts">Scripts</button>
    <button class="tab" data-tab="checks">Checks</button>
    <button class="tab" data-tab="audit">Audit: luxuryformen.com</button>
  </div>

  <div class="panel active" id="panel-overview">
    <div class="card" id="runtime-card">
      <h3>Runtime Status</h3>
      <div id="runtime-status"><span style="color:var(--text-dim)">Checking...</span></div>
    </div>
    <div class="card" style="margin-top:1rem">
      <h3>About</h3>
      <p id="about-text"></p>
    </div>
  </div>

  <div class="panel" id="panel-skills">
    <div class="cards" id="skills-grid"></div>
  </div>

  <div class="panel" id="panel-agents">
    <div class="cards" id="agents-grid"></div>
  </div>

  <div class="panel" id="panel-scripts">
    <ul class="script-list" id="script-list"></ul>
  </div>

  <div class="panel" id="panel-checks">
    <h3 style="margin-bottom:.75rem">Built-in Checks</h3>
    <div class="actions">
      <button class="btn" onclick="runCheck('portability')">Run Portability Check</button>
      <button class="btn sec" onclick="runCheck('doctor')">Run Doctor</button>
    </div>
    <div class="output" id="check-output">Select a check to run.</div>
  </div>

  <div class="panel" id="panel-audit">
    <div id="audit-content"><span style="color:var(--text-dim)">Loading audit...</span></div>
  </div>
</div>

<script>
const api = (p) => fetch('/api/' + p).then(r => r.json());

async function init() {
  const info = await api('info');
  document.getElementById('ver').textContent = 'v' + info.version;
  document.getElementById('desc').textContent = info.description;
  document.getElementById('about-text').textContent = info.description;

  const skills = await api('skills');
  const agents = await api('agents');
  const scripts = await api('scripts');
  const tests = await api('testcount');

  document.getElementById('stats').innerHTML = [
    s('Skills', skills.length),
    s('Agents', agents.length),
    s('Scripts', scripts.length),
    s('Tests', tests.count),
  ].join('');

  document.getElementById('skills-grid').innerHTML = skills.map(function(sk) {
    return '<div class="card"><h3>' + esc(sk.name) +
      (sk.invocable ? '<span class="badge">/seo</span>' : '') +
      '</h3><p>' + esc(sk.description) + '</p></div>';
  }).join('');

  document.getElementById('agents-grid').innerHTML = agents.map(function(a) {
    return '<div class="card"><h3>' + esc(a.name) +
      (a.model ? '<span class="badge">' + esc(a.model) + '</span>' : '') +
      '</h3><p>' + esc(a.description) + '</p></div>';
  }).join('');

  document.getElementById('script-list').innerHTML = scripts.map(function(sc) {
    return '<li>' + esc(sc) + '</li>';
  }).join('');

  const doctor = await api('doctor');
  var rs = document.getElementById('runtime-status');
  if (doctor.data) {
    var d = doctor.data;
    rs.innerHTML = [
      row(d.ready, 'Runtime: ' + (d.ready ? 'Ready' : 'Setup Required')),
      row(d.browser_ready, 'Chromium: ' + (d.browser_ready ? 'Ready' : 'Not Installed')),
      '<div class="status-row"><span style="color:var(--text-dim)">Python: ' + esc(d.python_version || '?') + '</span></div>',
      '<div class="status-row"><span style="color:var(--text-dim)">Mode: ' + esc(d.mode || '?') + '</span></div>',
    ].join('');
  } else {
    rs.innerHTML = '<p>' + esc(doctor.output || 'Unable to run doctor.') + '</p>';
  }
}

function s(label, n) {
  return '<div class="stat"><div class="num">' + n + '</div><div class="label">' + label + '</div></div>';
}
function row(ok, text) {
  return '<div class="status-row"><span class="dot ' + (ok ? 'green' : 'yellow') + '"></span>' + text + '</div>';
}
function esc(s) {
  var d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

async function runCheck(name) {
  var el = document.getElementById('check-output');
  el.className = 'output';
  el.textContent = 'Running ' + name + '...';
  var res = await api(name);
  el.className = 'output ' + (res.ok ? 'ok' : 'fail');
  if (res.data) el.textContent = JSON.stringify(res.data, null, 2);
  else el.textContent = res.output || JSON.stringify(res);
}

document.querySelectorAll('.tab').forEach(function(t) {
  t.addEventListener('click', function() {
    document.querySelectorAll('.tab').forEach(function(x) { x.classList.remove('active'); });
    document.querySelectorAll('.panel').forEach(function(x) { x.classList.remove('active'); });
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
  });
});

async function loadAudit() {
  var el = document.getElementById('audit-content');
  var audit = await api('audit');
  if (audit.error) { el.innerHTML = '<p>' + esc(audit.error) + '</p>'; return; }
  var s = audit.summary || {};
  var cats = audit.categories || [];
  var score = s.health_score || 0;
  var scoreColor = score >= 80 ? 'var(--accent2)' : score >= 60 ? 'var(--warn)' : 'var(--err)';

  var html = '<div class="card" style="text-align:center;margin-bottom:1rem">' +
    '<div style="font-size:3rem;font-weight:700;color:' + scoreColor + '">' + score + '/100</div>' +
    '<div style="color:var(--text-dim)">SEO Health Score — luxuryformen.com</div>' +
    '<div style="color:var(--text-dim);font-size:.85rem;margin-top:.5rem">Business type: ' + esc(s.business_type || '?') + '</div>' +
    '</div>';

  if (cats.length) {
    html += '<div class="cards" style="margin-bottom:1.5rem">';
    cats.forEach(function(c) {
      var cs = c.score || 0;
      var cc = cs >= 80 ? 'var(--accent2)' : cs >= 60 ? 'var(--warn)' : 'var(--err)';
      html += '<div class="card"><h3>' + esc(c.name) + '</h3>' +
        '<div style="font-size:1.5rem;font-weight:700;color:' + cc + '">' + cs + '/100</div>';
      if (c.findings && c.findings.length) {
        html += '<div style="margin-top:.5rem">';
        c.findings.forEach(function(f) {
          var emoji = {'Critical':'\uD83D\uDD34','High':'\uD83D\uDFE0','Medium':'\uD83D\uDFE1','Low':'\uD83D\uDD35'}[f.severity] || '\u2022';
          html += '<div style="font-size:.82rem;margin:.2rem 0">' + emoji + ' <strong>[' + esc(f.severity) + ']</strong> ' + esc(f.title) + '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
  }

  if (s.top_findings && s.top_findings.length) {
    html += '<div class="card"><h3>Top Findings</h3>';
    s.top_findings.forEach(function(tf) { html += '<div style="font-size:.85rem;margin:.2rem 0">\u2022 ' + esc(tf) + '</div>'; });
    html += '</div>';
  }

  el.innerHTML = html;
}

init();
loadAudit();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        routes = {
            "/": self._serve_html,
            "/api/info": self._serve_info,
            "/api/skills": lambda: self._serve_json(_get_skills()),
            "/api/agents": lambda: self._serve_json(_get_agents()),
            "/api/scripts": lambda: self._serve_json(_get_scripts()),
            "/api/doctor": lambda: self._serve_json(_run_doctor()),
            "/api/portability": lambda: self._serve_json(_run_portability()),
            "/api/testcount": lambda: self._serve_json({"count": _test_count()}),
            "/api/audit": lambda: self._serve_json(_get_audit()),
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(404)

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

    def _serve_info(self):
        info = _load_plugin_info()
        self._serve_json({
            "name": info.get("name", "claude-seo"),
            "version": info.get("version", "unknown"),
            "description": info.get("description", ""),
        })

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass  # quiet


def main():
    port = int(os.environ.get("PORT", "3000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Claude SEO dashboard running on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

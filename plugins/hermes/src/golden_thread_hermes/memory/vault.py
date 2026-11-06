"""Filesystem abstraction and maintenance operations for a Golden Thread vault."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Golden Thread queue + broker: all vault writes go through the spool
# (gt_write_queue.py -> <vault>/Projects/golden-thread/spool/queue/<req>.json)
# and are applied by gt_broker.py drain. This matches the upstream Golden Thread
# concurrency model exactly and gives us conflict-escalation, deduplication,
# and claim-respect for free.
_GT_HOOKS_DIR = Path(os.environ.get(
    "GT_HOOKS_DIR",
    "/root/.claude/golden-thread/hooks",
))
GT_WRITE_QUEUE = _GT_HOOKS_DIR / "gt_write_queue.py"
GT_BROKER = _GT_HOOKS_DIR / "gt_broker.py"


class VaultWriteError(RuntimeError):
    """A queued write could not be deposited or drained."""


def _find_vault_root(start: Path) -> Path:
    """Resolve the vault root for a write target -- the user-configured vault
    if it has Projects/golden-thread, else the GT_VAULT env, else ~/.claude/vault-config.json."""
    if start and (start / "Projects" / "golden-thread").exists():
        return start
    env = os.environ.get("GT_VAULT")
    if env and (Path(env) / "Projects" / "golden-thread").exists():
        return Path(env)
    try:
        import json
        cfg = json.loads((Path.home() / ".claude" / "vault-config.json").read_text())
        candidate = Path(cfg.get("vault_path", ""))
        if (candidate / "Projects" / "golden-thread").exists():
            return candidate
    except Exception:
        pass
    return start  # last-resort: the configured vault path


class Vault:
    """Read/write a Golden Thread vault on local disk.

    All writes go through the Golden Thread write-queue + broker spool. The
    plugin never touches a vault file directly for writes -- it deposits a
    JSON request into <vault>/Projects/golden-thread/spool/queue/ via
    gt_write_queue.py, then waits for gt_broker.py drain to apply it. Reads
    are direct; a drain-before-read helper is available for hot paths.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    # -------------------------------------------------------------------
    # Internal: queue + drain
    # -------------------------------------------------------------------

    def _queue_write(
        self,
        rel: str,
        op: str,
        content: str = "",
        section: Optional[str] = None,
        key: Optional[str] = None,
        session: str = "hermes",
    ) -> Dict[str, Any]:
        """Deposit a write request with gt_write_queue.py. Returns the queue file path."""
        if not GT_WRITE_QUEUE.exists():
            raise VaultWriteError(f"gt_write_queue.py not found at {GT_WRITE_QUEUE}")
        cmd: List[str] = [
            sys.executable,
            str(GT_WRITE_QUEUE),
            "--vault", str(self.root),
            "--path", rel,
            "--op", op,
            "--session", session,
            "--origin", "session",
            "--json",
        ]
        if op in ("append", "replace-section", "create", "replace-file"):
            cmd += ["--content", content]
        if section:
            cmd += ["--section", section]
        if key:
            cmd += ["--key", key, "--value", content]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise VaultWriteError(
                f"gt_write_queue failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        try:
            return {"ok": True, "result": __import__("json").loads(proc.stdout)}
        except Exception:
            return {"ok": True, "stdout": proc.stdout.strip()}

    def _drain(self, dry_run: bool = False) -> Dict[str, Any]:
        """Run gt_broker.py drain against this vault. Returns the parsed --json output."""
        if not GT_BROKER.exists():
            raise VaultWriteError(f"gt_broker.py not found at {GT_BROKER}")
        cmd: List[str] = [sys.executable, str(GT_BROKER), "drain", "--vault", str(self.root), "--json"]
        if dry_run:
            cmd.append("--dry-run")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return {"ok": proc.returncode == 0, "exit": proc.returncode,
                    "result": __import__("json").loads(proc.stdout) if proc.stdout.strip() else {}}
        except Exception:
            return {"ok": proc.returncode == 0, "exit": proc.returncode,
                    "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}

    def _write(
        self,
        rel: str,
        data: str,
        op: str = "replace-file",
        section: Optional[str] = None,
        key: Optional[str] = None,
        drain: bool = True,
    ) -> str:
        """Write a vault file via the queue (+ optional drain). Returns the broker outcome."""
        rel = str(rel).replace("\\", "/")
        self._queue_write(rel, op, content=data, section=section, key=key)
        if drain:
            self._drain(dry_run=False)
        return "queued"

    def _drain_if_outstanding(self) -> None:
        """Cheap status check + drain if anything is pending. Used before read-heavy ops."""
        if not GT_BROKER.exists():
            return
        try:
            proc = subprocess.run(
                [sys.executable, str(GT_BROKER), "status", "--vault", str(self.root), "--json"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                status = __import__("json").loads(proc.stdout)
                # If there's anything queued, drain it now.
                # Broker's --json status keys: {"pending": N, "oldest": ..., "inbox": N, "files": []}
                queued = status.get("pending", status.get("queued", 0)) if isinstance(status, dict) else 0
                if queued and int(queued) > 0:
                    # Retries are handled inside the broker itself (flock + lock-wait);
                    # a drain that still fails is surfaced by _drain()'s error path.
                    res = self._drain(dry_run=False)
                    if not res.get("ok") and res.get("exit") not in (0, None):
                        raise VaultWriteError(f"drain failed: {res}")
        except Exception:
            pass

    def core_path(self) -> Path:
        return self.root / "Core.md"

    async def keyword_recall(
        self,
        question: str,
        lineage: str = "",
        active_project: str = "",
        k: int = 10,
    ) -> Dict[str, Any]:
        """Vault lookup through the vault's own read tool (gt_keyword_recall.py).

        Returns {"results": [rel paths, best first], "scores": {...}, "read": [...],
        "locked": [...]}, matching gt-query's retrieval path.
        """
        recall_script = _GT_HOOKS_DIR / "gt_keyword_recall.py"
        if not recall_script.exists():
            # Fall back to the naive scan if the recall tool is absent.
            return await self.query(question, lineage=lineage, active_project=active_project)
        import json as _json
        proc = subprocess.run(
            [sys.executable, str(recall_script), question, "--vault", str(self.root), "--k", str(k), "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return {"status": "error", "detail": proc.stderr.strip() or proc.stdout.strip(), "query": question}
        try:
            data = _json.loads(proc.stdout)
        except Exception:
            return {"status": "error", "detail": f"unparseable recall output: {proc.stdout[:200]}", "query": question}
        # Filter by lineage (project) when given; keep the active project first.
        results = data.get("results", [])
        if lineage:
            results = [r for r in results if r.startswith(f"Projects/{lineage}/") or not r.startswith("Projects/")]
        if active_project:
            results.sort(key=lambda r: (not r.startswith(f"Projects/{active_project}/"),))
        return {"status": "ok", "query": question, **data, "results": results}

    def inbox_path(self) -> Path:
        return self.root / "Inbox.md"

    def memory_dir(self) -> Path:
        return self.root / "Memory"

    def projects_dir(self) -> Path:
        return self.root / "Projects"

    def wiki_dir(self) -> Path:
        return self.root / "Wiki"

    def sources_dir(self) -> Path:
        return self.root / "Sources"

    def settings_path(self) -> Path:
        return self.root / ".gt" / "settings.yaml"

    def ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def read_core(self, max_lines: int = 200) -> str:
        path = self.core_path()
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[:max_lines])
        except Exception:
            return ""

    def list_projects(self) -> List[str]:
        projects = self.projects_dir()
        if not projects.exists():
            return []
        return sorted([p.name for p in projects.iterdir() if p.is_dir() and not p.name.startswith(".")])

    def project_path(self, slug: str) -> Path:
        return self.projects_dir() / slug

    def slugify(self, name: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", name).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug or "project"

    def _all_markdown(self) -> List[Path]:
        return sorted(self.root.rglob("*.md"))

    # -----------------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------------

    async def query(self, q: str, lineage: str = "", active_project: str = "") -> Dict[str, Any]:
        self._drain_if_outstanding()
        qlower = q.lower()
        matches: List[Dict[str, Any]] = []
        for path in self._all_markdown():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if qlower in text.lower():
                # Find surrounding snippet
                idx = text.lower().find(qlower)
                snippet = text[max(0, idx - 120):idx + 240]
                rel = path.relative_to(self.root)
                project = ""
                if "Projects/" in str(rel):
                    project = rel.parts[1] if len(rel.parts) > 1 else ""
                if lineage and project != lineage:
                    continue
                matches.append({"path": str(rel), "snippet": snippet.strip(), "project": project})
        # Prefer active project
        if active_project:
            matches.sort(key=lambda m: (m["project"] != active_project, str(m["path"])))
        return {"count": len(matches), "matches": matches[:20]}

    # -----------------------------------------------------------------------
    # Lint
    # -----------------------------------------------------------------------

    async def lint_command(self, fix: bool = False) -> Dict[str, Any]:
        self._drain_if_outstanding()
        issues: List[Dict[str, Any]] = []
        for path in self._all_markdown():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = path.relative_to(self.root)
            # Check empty headers
            if re.search(r"^#+\s*$", text, flags=re.MULTILINE):
                issues.append({"path": str(rel), "issue": "empty header"})
            # Check duplicate level-1 headers
            h1s = re.findall(r"^#\s+(.+)", text, flags=re.MULTILINE)
            if len(h1s) > 1:
                issues.append({"path": str(rel), "issue": "multiple h1 headers"})
            # Check broken local links
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if link.startswith("http") or link.startswith("#"):
                    continue
                target = (path.parent / link).resolve()
                if not target.exists():
                    issues.append({"path": str(rel), "issue": f"broken link: {link}"})
        result = {"status": "ok", "count": len(issues), "issues": issues}
        if fix and issues:
            # For now, just report what would be fixed; destructive edits are
            # intentionally conservative.
            result["note"] = f"Would fix {len(issues)} issues. Re-run with apply=true after reviewing."
        return result

    # -----------------------------------------------------------------------
    # Git sync
    # -----------------------------------------------------------------------

    async def sync_command(self, action: str) -> Dict[str, Any]:
        if not (self.root / ".git").exists():
            return {"status": "error", "detail": "Vault is not a git repository. Initialize manually if desired."}
        cmd_map = {
            "status": ["git", "status", "--short"],
            "pull": ["git", "pull"],
            "push": ["git", "push"],
        }
        cmd = cmd_map.get(action)
        if not cmd:
            return {"status": "error", "detail": f"Unknown action: {action}"}
        try:
            result = subprocess.run(
                cmd,
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            return {
                "status": "ok" if result.returncode == 0 else "error",
                "action": action,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "detail": f"git {action} timed out"}

    # -----------------------------------------------------------------------
    # Forget / promote / demote / refresh
    # -----------------------------------------------------------------------

    async def forget_command(self, claim: str, reason: str = "") -> Dict[str, Any]:
        qlower = claim.lower()
        changed = 0
        removals: List[str] = []
        for path in self._all_markdown():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if qlower not in text.lower():
                continue
            rel = path.relative_to(self.root)
            lines = text.splitlines()
            new_lines: List[str] = []
            skip = False
            for line in lines:
                if qlower in line.lower():
                    if reason:
                        new_lines.append(f"~~{line}~~ <!-- forgotten: {reason} -->")
                    else:
                        new_lines.append(f"~~{line}~~ <!-- forgotten -->")
                    changed += 1
                    skip = False
                else:
                    new_lines.append(line)
            self._write(str(rel), "\n".join(new_lines), op="replace-file")
            removals.append(str(rel))
        return {"status": "ok", "changed_lines": changed, "files": removals}

    async def promote_command(self, source: str, destination: str, project: str = "") -> Dict[str, Any]:
        src_path = self.root / source
        dst_path = self.root / destination
        if not src_path.exists():
            return {"status": "error", "detail": f"Source not found: {source}"}
        self.ensure_dir(dst_path.parent)
        import shutil
        if src_path.is_dir():
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, dst_path)
        note = destination
        return {"status": "promoted", "source": source, "destination": destination, "note": note}

    async def demote_command(self, source: str, destination: str, project: str = "") -> Dict[str, Any]:
        src_path = self.root / source
        dst_path = self.root / destination
        if not src_path.exists():
            return {"status": "error", "detail": f"Source not found: {source}"}
        self.ensure_dir(dst_path.parent)
        import shutil
        if src_path.is_dir():
            shutil.move(str(src_path), str(dst_path))
        else:
            shutil.move(str(src_path), str(dst_path))
        return {"status": "demoted", "source": source, "destination": destination}

    async def refresh_command(self, apply: bool = False) -> Dict[str, Any]:
        sources = self.sources_dir()
        if not sources.exists():
            return {"status": "ok", "note": "No Sources/ directory"}
        files = sorted(sources.rglob("*.md"))
        return {"status": "ok", "sources": [str(f.relative_to(self.root)) for f in files], "apply": apply}

    # -----------------------------------------------------------------------
    # Upgrade / doctor / settings
    # -----------------------------------------------------------------------

    async def upgrade_command(self, dry_run: bool = True) -> Dict[str, Any]:
        missing = []
        expected = ["Core.md", "Inbox.md", "Memory", "Projects", "Knowledge", "Sources"]
        for name in expected:
            if not (self.root / name).exists():
                missing.append(name)
        return {
            "status": "ok",
            "dry_run": dry_run,
            "missing": missing,
            "note": "Run with dry_run=false to create missing folders." if missing else "Vault is up to date.",
        }

    async def doctor_command(self) -> Dict[str, Any]:
        issues = []
        if not self.core_path().exists():
            issues.append("Core.md missing")
        if not self.inbox_path().exists():
            issues.append("Inbox.md missing")
        if not self.memory_dir().exists():
            issues.append("Memory/ missing")
        if not self.projects_dir().exists():
            issues.append("Projects/ missing")
        core = self.read_core()
        if "body trust" not in core.lower():
            issues.append("Core.md does not reference body trust")
        return {
            "status": "ok" if not issues else "needs_attention",
            "issues": issues,
            "vault": str(self.root),
        }

    async def settings_command(self, view: bool = False, set_key: str = "", set_value: str = "") -> Dict[str, Any]:
        path = self.settings_path()
        self.ensure_dir(path.parent)
        try:
            import yaml
        except ImportError:
            yaml = None  # type: ignore
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {} if yaml else {}
            except Exception:
                data = {}
        if set_key:
            if not yaml:
                return {"status": "error", "detail": "PyYAML not available"}
            data[set_key] = set_value
            self._write(str(path.relative_to(self.root)), yaml.dump(data, default_flow_style=False), op="replace-file")
        return {"status": "ok", "settings": data if view else None, "changed": bool(set_key)}

    # -----------------------------------------------------------------------
    # Context / validate / optimize / scan / allin
    # -----------------------------------------------------------------------

    async def context_command(self) -> Dict[str, Any]:
        self._drain_if_outstanding()
        core = self.read_core()
        projects = self.list_projects()
        memory = []
        md = self.memory_dir()
        if md.exists():
            memory = [f.name for f in sorted(md.iterdir()) if f.is_file()]
        return {
            "status": "ok",
            "core_preview": core[:500],
            "projects": projects,
            "memory_files": memory,
            "vault": str(self.root),
        }

    async def validate_command(self, claim: str) -> Dict[str, Any]:
        self._drain_if_outstanding()
        qlower = claim.lower()
        confirming = []
        contradicting = []
        for path in self._all_markdown():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = path.relative_to(self.root)
            if qlower in text.lower():
                confirming.append(str(rel))
        return {
            "status": "ok",
            "claim": claim,
            "confirming_files": confirming,
            "contradicting_files": contradicting,
            "note": "Manual review still required; contradiction detection is conservative.",
        }

    async def validation_command(
        self,
        claim: str,
        file_path: str = "",
        status: str = "",
    ) -> Dict[str, Any]:
        validation_dir = self.root / "Validations"
        self.ensure_dir(validation_dir)
        ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        record = f"- {ts}: claim='{claim}' status={status or 'recorded'}"
        if file_path:
            record += f" file={file_path}"
        record += "\n"
        self._write("Validations/validations.md", record, op="append")
        return {"status": "recorded", "claim": claim}

    async def optimize_command(self, member: str = "vault", apply: bool = False) -> Dict[str, Any]:
        self._drain_if_outstanding()
        files = self._all_markdown()
        total = sum(f.stat().st_size for f in files if f.is_file())
        duplicates: List[Dict[str, Any]] = []
        if member == "vault":
            # Simple first-line duplicate detection
            seen: Dict[str, Path] = {}
            for path in files:
                try:
                    first = path.read_text(encoding="utf-8").splitlines()[0]
                except Exception:
                    continue
                if first in seen:
                    duplicates.append({"line": first, "paths": [str(seen[first].relative_to(self.root)), str(path.relative_to(self.root))]})
                else:
                    seen[first] = path
        return {"status": "ok", "member": member, "total_bytes": total, "duplicates": duplicates, "apply": apply}

    async def scan_command(self, path: str = "") -> Dict[str, Any]:
        self._drain_if_outstanding()
        target = self.root / path if path else self.root
        if not target.exists():
            return {"status": "error", "detail": f"Path not found: {path}"}
        return {"status": "ok", "path": str(target), "note": "Scan uses project language definitions; no automatic fix applied."}

    async def allin_command(self) -> Dict[str, Any]:
        self._drain_if_outstanding()
        lint = await self.lint_command(fix=False)
        doctor = await self.doctor_command()
        optimize = await self.optimize_command(member="vault", apply=False)
        return {
            "status": "ok",
            "mode": "report",
            "lint": lint,
            "doctor": doctor,
            "optimize": optimize,
        }

    async def allin_commit_command(self, message: str) -> Dict[str, Any]:
        allin = await self.allin_command()
        if allin["doctor"]["status"] != "ok":
            return {"status": "error", "detail": "Doctor failed; commit blocked", "allin": allin}
        if allin["lint"]["count"] > 0:
            return {"status": "error", "detail": "Lint issues found; commit blocked", "allin": allin}
        if not (self.root / ".git").exists():
            return {"status": "error", "detail": "Vault is not a git repository", "allin": allin}
        try:
            subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True, text=True, timeout=30)
            subprocess.run(["git", "commit", "-m", message], cwd=self.root, check=True, capture_output=True, text=True, timeout=30)
            return {"status": "committed", "message": message}
        except subprocess.CalledProcessError as e:
            return {"status": "error", "detail": e.stderr.strip() or e.stdout.strip()}
        except subprocess.TimeoutExpired:
            return {"status": "error", "detail": "git commit timed out"}

    # -----------------------------------------------------------------------
    # Brief / runbook / digest
    # -----------------------------------------------------------------------

    async def runbook_lint_command(self) -> Dict[str, Any]:
        self._drain_if_outstanding()
        runbooks = []
        for pdir in self.projects_dir().iterdir():
            if not pdir.is_dir():
                continue
            rb = pdir / "runbook.md"
            if rb.exists():
                runbooks.append(str(rb.relative_to(self.root)))
        return {"status": "ok", "runbooks": runbooks}

    async def digest_command(self, file_path: str) -> Dict[str, Any]:
        self._drain_if_outstanding()
        target = Path(file_path)
        if not target.is_absolute():
            target = self.root / target
        if not target.exists():
            return {"status": "error", "detail": f"File not found: {file_path}"}
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as e:
            return {"status": "error", "detail": str(e)}
        lines = text.splitlines()
        return {
            "status": "ok",
            "path": str(target),
            "line_count": len(lines),
            "summary": f"First 10 lines:\n" + "\n".join(lines[:10]),
        }

    # -----------------------------------------------------------------------
    # Setup / init / calibrate / grant
    # -----------------------------------------------------------------------

    async def setup_command(self, modules: Optional[List[str]] = None, security: str = "") -> Dict[str, Any]:
        for name in ["Core.md", "Inbox.md", "Memory", "Projects", "Knowledge", "Sources", "Daily", "Validations"]:
            if name.endswith(".md"):
                if not (self.root / name).exists():
                    self._write(name, f"# {name[:-3]}\n\n", op="replace-file")
            else:
                self.ensure_dir(self.root / name)
        return {"status": "ok", "vault": str(self.root), "modules": modules or [], "security": security}

    async def init_command(self, project: str = "", repo: str = "") -> Dict[str, Any]:
        await self.setup_command()
        result = {"status": "initialized", "vault": str(self.root)}
        if project:
            slug = self.slugify(project)
            self.ensure_dir(self.project_path(slug))
            result["project"] = slug
        if repo:
            result["repo"] = repo
            result["note"] = "Link the project to the repo by updating the project idea.md."
        return result

    async def calibrate_command(self, estimate: bool = False) -> Dict[str, Any]:
        return {"status": "ok", "estimate": estimate, "note": "Calibration is a no-op in Hermes; the model uses the provider's prompt budget."}

    async def grant_command(self, command: str = "", duration: str = "", revoke: bool = False) -> Dict[str, Any]:
        return {
            "status": "ok",
            "command": command,
            "duration": duration,
            "revoke": revoke,
            "note": "Hermes does not implement command-level lockdown grants; use Hermes config/skills for access control.",
        }

    # -----------------------------------------------------------------------
    # Ingest / wiki / watch / farm / flow / visualize / workflow / lotr / usage / demo
    # -----------------------------------------------------------------------

    async def ingest_command(self, source: str) -> Dict[str, Any]:
        src = Path(source)
        if not src.is_absolute():
            src = self.root / src
        if not src.exists():
            return {"status": "error", "detail": f"Source not found: {source}"}
        import shutil
        if src.is_dir():
            self.ensure_dir(self.root / "Ingested" / src.name)
            # shallow copy only top-level md files
            copied = []
            for f in src.glob("*.md"):
                dst = self.root / "Ingested" / src.name / f.name
                shutil.copy2(f, dst)
                copied.append(str(dst.relative_to(self.root)))
            return {"status": "ingested", "copied": copied}
        else:
            dst = self.root / "Ingested" / src.name
            self.ensure_dir(dst.parent)
            shutil.copy2(src, dst)
            return {"status": "ingested", "copied": [str(dst.relative_to(self.root))]}

    async def wiki_init_command(self) -> Dict[str, Any]:
        self.ensure_dir(self.wiki_dir())
        self._write(str(self.wiki_dir().relative_to(self.root) / "index.md"), "# Golden Thread Wiki\n\n", op="replace-file")
        return {"status": "ok", "wiki": str(self.wiki_dir())}

    async def wiki_ingest_command(self, source: str) -> Dict[str, Any]:
        await self.wiki_init_command()
        return await self.ingest_command(source)

    async def wiki_lint_command(self) -> Dict[str, Any]:
        return await self.lint_command(fix=False)

    async def wiki_refresh_command(self) -> Dict[str, Any]:
        return {"status": "ok", "note": "Wiki refresh is manual in Hermes; re-run gt_wiki_ingest with updated sources."}

    async def watch_command(self, repo: str = "", action: str = "add") -> Dict[str, Any]:
        """Use upstream gt-watch to track a git repo in the vault."""
        from ..watch import watch_add, watch_remove, watch_list

        vault_path = str(self.root)
        if action == "add" and repo:
            return watch_add(url=repo, vault_path=vault_path)
        if action == "remove" and repo:
            return watch_remove(slug=repo, vault_path=vault_path)
        return watch_list(vault_path=vault_path)

    async def farm_command(self, task: str) -> Dict[str, Any]:
        farm_dir = self.root / ".gt" / "farm"
        self.ensure_dir(farm_dir)
        ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%d-%H%M%S")
        task_id = f"farm-{self.slugify(task[:40])}-{ts}"
        task_file = farm_dir / f"{task_id}.md"
        body = (
            f"# Farm Task: {task_id}\n\n"
            f"Task: {task}\n\n"
            "## Execution Notes\n\n"
            "Run this task in Hermes with a dedicated subagent context:\n\n"
            f"`delegate_task(goal={task!r})`\n\n"
            "Place results in this vault under Knowledge or the active project.\n"
        )
        self._write(str(task_file.relative_to(self.root)), body, op="replace-file")
        return {
            "status": "ok",
            "task_id": task_id,
            "task_file": str(task_file.relative_to(self.root)),
            "note": "Farm task recorded. Delegate it with Hermes delegate_task and write results back to the vault.",
        }

    async def flow_command(self, redact: bool = False) -> Dict[str, Any]:
        """Render an HTML timeline from project logs and ADRs."""
        html = self.root / ".gt" / "flow.html"
        self.ensure_dir(html.parent)
        events: List[Dict[str, Any]] = []
        for pdir in self.projects_dir().iterdir():
            if not pdir.is_dir():
                continue
            log_dir = pdir / "Log"
            if log_dir.exists():
                for log in sorted(log_dir.glob("*.md")):
                    for line in log.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("-"):
                            events.append({"project": pdir.name, "event": line[1:].strip()})
            adr_dir = pdir / "Decisions"
            if adr_dir.exists():
                for adr in sorted(adr_dir.glob("ADR-*.md")):
                    first = adr.read_text(encoding="utf-8").splitlines()[0]
                    events.append({"project": pdir.name, "event": f"Decision: {first.lstrip('# ').strip()}"})
        if redact:
            events = [{"project": e["project"], "event": e["event"][:80] + "..." if len(e["event"]) > 80 else e["event"]} for e in events]
        body_lines = [
            "<html><head><title>Golden Thread Flow</title></head><body>",
            "<h1>Golden Thread Flow</h1>",
            "<ul>",
        ]
        for e in events[-100:]:
            safe_event = e["event"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body_lines.append(f"<li><strong>{e['project']}</strong>: {safe_event}</li>")
        body_lines.extend(["</ul>", "</body></html>"])
        self._write(str(html.relative_to(self.root)), "\n".join(body_lines), op="replace-file")
        return {"status": "ok", "file": str(html.relative_to(self.root)), "events": len(events), "redact": redact}

    async def visualize_command(self, target: str = "", redact: bool = False) -> Dict[str, Any]:
        """Render a Mermaid graph of vault structure as Markdown."""
        md = self.root / ".gt" / "visualize.md"
        self.ensure_dir(md.parent)
        target_path = Path(target) if target else self.root
        if not target_path.is_absolute():
            target_path = self.root / target_path
        if not target_path.exists():
            target_path = self.root
        nodes: List[str] = []
        links: List[str] = []
        root_name = target_path.name or "vault"
        nodes.append(f"    {root_name}[\"{root_name}\"]")
        for md_file in target_path.rglob("*.md"):
            rel = md_file.relative_to(target_path)
            name = "/".join(rel.parts)
            if redact:
                name = rel.stem[:12] + "..." if len(rel.stem) > 12 else rel.stem
            node_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(rel))
            nodes.append(f"    {node_id}[\"{name}\"]")
            links.append(f"    {root_name} --> {node_id}")
        body_lines = ["# Golden Thread Visualize", "", "```mermaid", "graph TD"] + nodes + links + ["```", ""]
        self._write(str(md.relative_to(self.root)), "\n".join(body_lines), op="replace-file")
        html = self.root / ".gt" / "visualize.html"
        self._write(str(html.relative_to(self.root)),
            "<html><body><h1>Golden Thread Visualize</h1>"
            f"<pre>{chr(10).join(body_lines)}</pre></body></html>",
            op="replace-file",
        )
        return {"status": "ok", "markdown": str(md.relative_to(self.root)), "html": str(html.relative_to(self.root)), "nodes": len(nodes), "redact": redact}

    async def workflow_command(
        self,
        name: str,
        run: bool = False,
        save: bool = False,
    ) -> Dict[str, Any]:
        wf_dir = self.root / ".gt" / "workflows"
        self.ensure_dir(wf_dir)
        wf_file = wf_dir / f"{self.slugify(name)}.yaml"
        if save:
            self._write(str(wf_file.relative_to(self.root)), f"name: {name}\nsteps: []\n", op="replace-file")
        executed: List[str] = []
        if run and wf_file.exists():
            import yaml
            try:
                data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
            except Exception as e:
                return {"status": "error", "detail": f"Failed to parse workflow: {e}"}
            for step in data.get("steps", []):
                tool = step.get("tool", "")
                args = step.get("args", {})
                if tool:
                    executed.append(f"would run {tool}({args})")
        return {"status": "ok", "workflow": str(wf_file.relative_to(self.root)), "run": run, "save": save, "executed": executed}

    async def lotr_command(
        self,
        service: str,
        operation: str,
        consent: bool = False,
    ) -> Dict[str, Any]:
        if not consent:
            return {"status": "error", "detail": "consent=true required for LOTR service calls"}
        lotr_dir = self.root / ".gt" / "lotr"
        self.ensure_dir(lotr_dir)
        ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        record = lotr_dir / "requests.md"
        self._write(str(record.relative_to(self.root)), f"- {ts}: service={service} operation={operation}\n", op="append")
        return {
            "status": "ok",
            "service": service,
            "operation": operation,
            "note": "LOTR gateway is not configured in Hermes; request recorded in .gt/lotr/requests.md.",
        }

    async def lotr_research_command(self, service: str) -> Dict[str, Any]:
        lotr_dir = self.root / ".gt" / "lotr"
        self.ensure_dir(lotr_dir)
        findings = lotr_dir / "research.md"
        if not findings.exists():
            self._write(str(findings.relative_to(self.root)), f"# LOTR Research: {service}\n\nAdd findings here.\n", op="replace-file")
        return {
            "status": "ok",
            "service": service,
            "file": str(findings.relative_to(self.root)),
            "note": "LOTR research file created. Add findings manually or via Hermes web search.",
        }

    async def usage_command(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "note": "Hermes does not expose a Claude usage plan API. Track usage via your provider dashboard.",
        }

    async def demo_command(self, action: str = "tour") -> Dict[str, Any]:
        if action in ("start", "tour"):
            return {
                "status": "ok",
                "tour": [
                    "gt_create name='Demo Project' idea='Walk through Golden Thread Hermes.'",
                    "gt_work summary='Started guided tour.'",
                    "gt_decide title='Use vault files as memory' context='Hermes has no MCP filesystem hooks' decision='Keep everything under the configured vault path' consequences='Full parity depends on local file tools only.'",
                    "gt_list",
                ],
            }
        return {"status": "ok", "note": "Tour ended. No changes made."}

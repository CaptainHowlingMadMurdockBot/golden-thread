"""A downstream MCP server that speaks stdio (0.5.0, owner 2026-10-13: "stdio is actually what it
is called").

An `mcp` connection with `transport: "stdio"` is a local command LOTR launches itself and talks to
over the child's stdin/stdout: MCP's stdio transport, one JSON-RPC message per line (newline
delimited, no embedded newlines). Nothing is listened on, nothing crosses the network.

Connection fields (registry.py validates them; the command comes from the owner's registry only,
never from a caller):

    command   argv list, e.g. ["python3", "-I", "/abs/server.py"]. NO shell on any platform.
    env       {NAME: secret REF}. Refs only (store:, keychain:, file:, ...), resolved when the
              process starts and never written back, logged or returned. The child gets a small
              base environment (PATH, HOME, temp dirs, locale ...), NOT this process's whole one.
    cwd       optional absolute directory the command starts in.
    timeouts  optional {start_s, call_s, idle_s}: spawn + initialize (30), one request (120),
              how long an unused process is kept (600). Bounded 1..3600.

Lifecycle:
    start     lazily, on the first find-refresh / call; `initialize` + `notifications/initialized`.
    reuse     one process per connection id, kept between calls, restarted when the registry's
              command/env/cwd changes, when it died, or when it sat idle past idle_s.
    shutdown  at interpreter exit (atexit) and by shutdown_all(): stdin closed, 2s grace,
              terminate, kill. A timed-out request kills the process (its state is unknown); the
              next call starts a fresh one.
    stderr    drained into a bounded in-memory tail (8 KiB), values of the env refs masked. It is
              never echoed to the terminal and never put into an error message.
    retry     a process that died mid-request is restarted and the request retried ONCE only for
              tools/list and read-tier tools; a write or consent call is never replayed.

Server-to-client requests (sampling, roots, ...) are answered "method not found": LOTR advertises
no client capabilities.
"""
import atexit
import collections
import json
import os
import queue
import shutil
import subprocess
import threading
import time

from . import procgroup
from .errors import GatewayError

PROTOCOL = "2025-03-26"
DEFAULT_TIMEOUTS = {"start_s": 30, "call_s": 120, "idle_s": 600}
MAX_LINE_BYTES = 8 * 1024 * 1024
STDERR_TAIL_BYTES = 8192
_BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
             "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "PATHEXT", "APPDATA", "LOCALAPPDATA",
             "PROGRAMDATA", "SHELL")
_EOF = object()


def timeouts_of(conn):
    t = dict(DEFAULT_TIMEOUTS)
    t.update(conn.get("timeouts") or {})
    return t


def _fingerprint(conn):
    return json.dumps([conn.get("command"), conn.get("env"), conn.get("env_plain"), conn.get("cwd")],
                      sort_keys=True)


class _Died(Exception):
    pass


class _Proc:
    def __init__(self, conn, env_values, resolved_command):
        self.cid = conn["id"]
        self.fingerprint = _fingerprint(conn)
        self.t = timeouts_of(conn)
        self._mask = [v for v in env_values.values() if len(v) >= 4]
        env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
        env.update({k: v for k, v in (conn.get("env_plain") or {}).items() if isinstance(v, str)})
        env.update(env_values)
        try:
            self.p = subprocess.Popen(
                resolved_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=conn.get("cwd") or None, env=env, shell=False,
                bufsize=0, close_fds=True, **procgroup.popen_kwargs())
        except (OSError, ValueError) as e:
            raise GatewayError("unreachable", f"{self.cid}: cannot start the command "
                                              f"({type(e).__name__})",
                               hints=["check `command` and `cwd` in the connection"])
        self._tree = procgroup.attach(self.p)
        self.started = time.time()
        self.last_used = self.started
        self._inbox = queue.Queue()
        self._err = collections.deque()
        self._err_len = 0
        self.stderr_total = 0
        self._next_id = 0
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    # -- reader threads ----------------------------------------------------------------------

    def _pump_out(self):
        try:
            while True:
                line = self.p.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    self.kill()
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue                     # a stray log line on stdout is not a message
                if isinstance(msg, dict):
                    self._inbox.put(msg)
        except (OSError, ValueError):
            pass
        self._inbox.put(_EOF)

    def _pump_err(self):
        try:
            while True:
                chunk = self.p.stderr.read(1024)
                if not chunk:
                    break
                self.stderr_total += len(chunk)
                self._err.append(chunk)
                self._err_len += len(chunk)
                while self._err_len > STDERR_TAIL_BYTES and len(self._err) > 1:
                    self._err_len -= len(self._err.popleft())
        except (OSError, ValueError):
            pass

    def stderr_tail(self, limit=300):
        text = b"".join(self._err).decode("utf-8", errors="replace")
        for m in self._mask:
            text = text.replace(m, "***")
        return text[-limit:]

    # -- protocol ----------------------------------------------------------------------------

    def alive(self):
        return self.p.poll() is None

    def _send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self.p.stdin.write(data)
            self.p.stdin.flush()
        except (OSError, ValueError):
            raise _Died()

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def rpc(self, method, params, timeout):
        self._next_id += 1
        want = self._next_id
        msg = {"jsonrpc": "2.0", "id": want, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.kill()
                raise GatewayError("downstream_error", f"{self.cid}: {method} timed out after "
                                                       f"{timeout}s; the process was stopped")
            try:
                m = self._inbox.get(timeout=left)
            except queue.Empty:
                continue
            if m is _EOF:
                self._inbox.put(_EOF)
                raise _Died()
            if "method" in m and "id" in m:      # a request from the server: we offer nothing
                try:
                    self._send({"jsonrpc": "2.0", "id": m["id"],
                                "error": {"code": -32601, "message": "method not found"}})
                except _Died:
                    raise
                continue
            if m.get("id") == want and "method" not in m:
                if m.get("error"):
                    err = m["error"] if isinstance(m["error"], dict) else {}
                    raise GatewayError("downstream_error", f"{self.cid}: {method} failed: "
                                       f"{str(err.get('message') or m['error'])[:300]}")
                res = m.get("result")
                return res if isinstance(res, dict) else {}

    def handshake(self):
        try:
            self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "gt-lotr", "version": "0.5.0"}},
                     self.t["start_s"])
            self.notify("notifications/initialized")
        except _Died:
            self.kill()
            raise GatewayError("unreachable", f"{self.cid}: the process exited while starting"
                                              f" (code {self.p.poll()})",
                               hints=["run the command by hand to see why; its stderr is not shown"])

    def kill(self):
        try:
            if self.p.poll() is None:
                self.p.kill()
            procgroup.kill(self._tree)            # and anything the server started
        except OSError:
            pass

    def close(self):
        try:
            self.p.stdin.close()
        except (OSError, ValueError):
            pass
        for step in (None, "terminate", "kill"):
            if step:
                try:
                    getattr(self.p, step)()
                except OSError:
                    pass
            try:
                self.p.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue
        procgroup.kill(self._tree)                # a grandchild must not outlive its server
        procgroup.release(self._tree)
        self._tree = None
        for s in (self.p.stdout, self.p.stderr):
            try:
                s.close()
            except (OSError, ValueError):
                pass


# -- the process table: one process per connection id, shared by every connection object -------

_LOCK = threading.RLock()
_PROCS = {}


def _resolve_command(conn, env_values):
    argv = list(conn["command"])
    first = argv[0]
    if not os.path.isabs(first):
        path = env_values.get("PATH") or os.environ.get("PATH") or os.defpath
        found = shutil.which(first, path=path)
        if not found:
            from . import runtimes
            r = runtimes.check(conn.get("runtime"), argv, path=path)
            raise GatewayError("unreachable",
                               f"{conn['id']}: command {first!r} was not found",
                               hints=[r.get("fix") or "use an absolute path in `command`"])
        argv[0] = found
    return argv


def reap_idle(now=None):
    now = now if now is not None else time.time()
    with _LOCK:
        for cid, pr in list(_PROCS.items()):
            if not pr.alive() or now - pr.last_used > pr.t["idle_s"]:
                _PROCS.pop(cid, None)
                pr.close()


def shutdown_all():
    with _LOCK:
        procs = list(_PROCS.values())
        _PROCS.clear()
    for pr in procs:
        pr.close()


def forget(cid):
    with _LOCK:
        pr = _PROCS.pop(cid, None)
    if pr:
        pr.close()


def process_info(cid):
    with _LOCK:
        pr = _PROCS.get(cid)
        if pr is None:
            return {"running": False}
        running = pr.alive()
        info = {"running": running, "pid": pr.p.pid, "started": int(pr.started),
                "stderr_bytes": pr.stderr_total}
        if not running:
            info["exit_code"] = pr.p.poll()
            info["stderr_tail"] = pr.stderr_tail()
        return info


atexit.register(shutdown_all)


class McpStdioConnection:
    """Same interface as McpConnection / HttpConnection: discover() and call(op, args)."""

    def __init__(self, conn, profile, *, secret_resolver=None, timeout=None, **_ignored):
        if secret_resolver is None:
            from .secrets import resolve as secret_resolver
        self.conn = conn
        self.profile = profile or {}
        self._resolve = secret_resolver
        self.cid = conn["id"]

    def _env_values(self):
        out = {}
        for name, ref in (self.conn.get("env") or {}).items():
            value = self._resolve(ref)
            if not isinstance(value, str) or not value or "\x00" in value:
                raise GatewayError("secret_unreadable",
                                   f"{self.cid}: env {name} (ref {ref}) did not resolve")
            out[name] = value
        return out

    def _proc(self):
        reap_idle()
        with _LOCK:
            pr = _PROCS.get(self.cid)
            if pr is not None and (not pr.alive() or pr.fingerprint != _fingerprint(self.conn)):
                _PROCS.pop(self.cid, None)
                pr.close()
                pr = None
            if pr is None:
                env_values = self._env_values()
                pr = _Proc(self.conn, env_values, _resolve_command(self.conn, env_values))
                try:
                    pr.handshake()
                except BaseException:
                    pr.close()
                    raise
                _PROCS[self.cid] = pr
            pr.last_used = time.time()
            return pr

    def _run(self, fn, retry):
        for attempt in (1, 2):
            pr = self._proc()
            with _LOCK:
                try:
                    return fn(pr)
                except _Died:
                    code = pr.p.poll()
                    _PROCS.pop(self.cid, None)
                    pr.close()
                    if retry and attempt == 1:
                        continue
                    raise GatewayError("unreachable", f"{self.cid}: the process exited"
                                                      f" (code {code}) before it answered",
                                       hints=["run `lotr status --check` to restart it"])
                except GatewayError:
                    if not pr.alive():
                        _PROCS.pop(self.cid, None)
                        pr.close()
                    raise

    def discover(self):
        def run(pr):
            tools, cursor = [], None
            for _ in range(50):
                result = pr.rpc("tools/list", {"cursor": cursor} if cursor else {},
                                pr.t["call_s"])
                tools += [t for t in result.get("tools") or [] if isinstance(t, dict)]
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            return tools
        return self._run(run, retry=True)

    def call(self, op, args, *, cursor=None):
        name = op.get("name")
        if not name or op.get("method") != "MCP":
            raise GatewayError("bad_op", f"{self.cid} is an MCP connection: call one of "
                                         "its tools by name (see find)")

        def run(pr):
            result = pr.rpc("tools/call", {"name": name, "arguments": dict(args or {})},
                            pr.t["call_s"])
            texts = [c.get("text", "") for c in result.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text"]
            if result.get("isError"):
                raise GatewayError("downstream_error",
                                   f"{self.cid}: {name} failed: {' '.join(texts)[:300]}")
            data = result.get("structuredContent")
            if data is None:
                joined = "\n".join(texts)
                try:
                    data = json.loads(joined) if len(texts) == 1 else joined
                except ValueError:
                    data = joined
            return {"status": 200, "data": data, "next_cursor": None, "notes": []}
        return self._run(run, retry=op.get("tier") == "read")

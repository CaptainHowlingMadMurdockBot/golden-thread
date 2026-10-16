#!/usr/bin/env python3
"""lotr: the gt-lotr command line.

    lotr [--home H | --zone Z] <command> ...

Call plane (goes through the daemon, or the hub in mode client):
    find [QUERY] [--connection C] [--limit N] [--schema] [--role reader|writer|combined]
    describe CONN OP [--role R]     (0.5.0: what one op does, its inputs, its risk, and a
                                     single-use reference to run it by: pass the reference as OP)
    read|write|consent CONN OP [--args JSON] [k=v ...] [--select S] [--cursor X]
    status
    catalog

Admin plane (edits the files under the home directly; refused in mode client):
    init --zone Z --mode local|hub|client
    add-http ID --profile P --base-url U --identity S --auth bearer|basic|header|none
             [--token-ref R] [--user U] [--header H] [--description D] [--trust T]
    add-mcp  ID --endpoint URL --identity S [--auth-ref file:/abs/tokens.json#access_token]
             [--refresh-cmd "<the MCP client's refresh helper>"]   (0.2.0: SSO/OAuth MCP)
             [--from-template T] [--auth-header NAME]   (0.5.0: kind mcp-http, a static key)
    connect NAME (--url HTTPS_MCP_ENDPOINT | --from-template T) [--scope S] [--client-id ID] [--identity S] ...
             (0.3.0: LOTR's own OAuth: signs in at THIS terminal, stores the refresh token
              sealed (gt unlock on) or in a mode-600 store file, registers the connection)
             (0.5.0: --allow-insecure-metadata HOST, repeatable: follow that host's plain-http
              OAuth metadata document; the token and authorize endpoints must still be https)
    add-stdio ID --command "PROGRAM ARGS" --identity S [--env NAME=REF ...] [--cwd DIR]
             [--from-template T]   (0.5.0: an MCP server that speaks stdio; LOTR launches it)
    pull [--import NAME ...] [--dry-run] [--yes]   (0.5.0: the MCP servers Claude Code already has,
             and the command that brings each into LOTR; read-only unless you --import one)
    template guide|new|check|list   (0.5.0: write your own connection template from API docs)
    template facts-check FILE       (0.5.0: check a research run's fact sheet)
    template update [--ref R] [--dry-run]   (0.5.0: fetch the template catalog; creates nothing)
    setup TEMPLATE [--id name@zone]  (0.5.0: check, credential, add, smoke-test: a service in one go)
    secret put NAME | check REF     (0.5.0: put a credential in the store from YOUR terminal)
    login NAME [--scope S]        re-authenticate (adds scopes; never narrows)
    disconnect NAME               revoke at the server, delete the stored secrets, unregister
    enroll CLIENT --machine M [--allow GLOB ...] [--max-tier T] --secret-out PATH
    revoke CLIENT
    connections

Stdout is JSON. Exit 0 on success, 1 on a gateway error, 2 on a usage error.
`enroll` writes the new client secret to --secret-out (mode 600) and never prints it.

0.5.0: a hub also speaks MCP over HTTP (POST /mcp, for a plain MCP client: any MCP-over-HTTP connector). It is
off: `init --mode hub` writes `listen.mcp_http` and `listen.mcp_http_trust_loopback`, both false.
Set `listen.mcp_http` to true in gateway.json, `enroll` the client, restart lotrd; the client sends
`Authorization: Bearer <secret>` and `X-GT-Client: <id>`. A connector that cannot send headers needs
`listen.mcp_http_trust_loopback` true too, and then names the client as `?cid=<id>` from this machine.

0.3.0: while gt unlock is on, `enroll` and `revoke` first need scope gt:hub:enroll from gt
core's unlock authority with a STEP-UP (a fresh Touch ID / Windows Hello, raised by the
authority; a TOTP code may be typed at this terminal). Nothing is written before it answers.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lotrlib.errors import GatewayError          # noqa: E402
from lotrlib.util import lotr_home, read_json, write_json, has_terminal   # noqa: E402

MODES = ("local", "hub", "client", "hybrid")
ARCHETYPE_NAMES = ("stdio-npx", "stdio-uvx", "stdio-docker", "stdio-binary", "mcp-oauth",
                   "mcp-oauth-preregistered", "mcp-http-key", "http-rest", "sse-bridge")


def _out(obj):
    sys.stdout.write(json.dumps(obj, indent=2) + "\n")
    sys.stdout.flush()


def _home(ns, zone=None):
    if ns.home:
        return Path(ns.home).expanduser()
    return lotr_home(zone or ns.zone)


def _parse_kv(pairs):
    out = {}
    for p in pairs:
        if "=" not in p:
            raise _Usage(f"expected k=v, got {p!r}")
        k, v = p.split("=", 1)
        if not k:
            raise _Usage(f"empty key in {p!r}")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


class _Usage(Exception):
    pass


# ------------------------------------------------------------------ call plane

def _client(ns):
    from lotrlib.client import Client
    return Client.from_home(_home(ns))


def cmd_find(ns):
    params = {"query": ns.query or "", "detail": "schema" if ns.schema else "summary"}
    if ns.connection:
        params["connection"] = ns.connection
    if ns.limit is not None:
        params["limit"] = ns.limit
    if getattr(ns, "role", None):
        params["role"] = ns.role
    return _client(ns).request("find", params)


def cmd_describe(ns):
    params = {"connection": ns.conn, "op": ns.op}
    if ns.role:
        params["role"] = ns.role
    return _client(ns).request("describe", params)


def cmd_call(ns):
    args = {}
    if ns.args:
        try:
            args = json.loads(ns.args)
        except ValueError:
            raise _Usage("--args is not valid JSON")
        if not isinstance(args, dict):
            raise _Usage("--args must be a JSON object")
    args.update(_parse_kv(ns.kv))
    params = {"tool": "call_" + ns.cmd, "connection": ns.conn, "op": ns.op, "args": args}
    if ns.select:
        params["select"] = ns.select
    if ns.cursor:
        params["cursor"] = ns.cursor
    return _client(ns).request("call", params)


def cmd_status(ns):
    r = _client(ns).request("status", {"check": bool(getattr(ns, "check", False))})
    if getattr(ns, "table", False):
        from lotrlib import status_view
        return _Text(status_view.render(r))
    return r


class _Text(str):
    """A human view (lotr status --table): printed as it is, not as JSON."""


def cmd_catalog(ns):
    return _client(ns).request("catalog", {})


# ------------------------------------------------------------------ admin plane

def _admin_home(ns):
    home = _home(ns)
    cfg = read_json(home / "gateway.json") if (home / "gateway.json").exists() else None
    if cfg is None:
        raise GatewayError("not_initialized", f"no gateway.json in {home}",
                           hints=[f"lotr --home {home} init --zone personal --mode local"])
    if cfg.get("mode") == "client":
        raise GatewayError("admin_refused",
                           "this home is in mode client; administer the hub instead")
    return home


def _registry(home):
    from lotrlib.registry import Registry       # lazy: only the admin plane needs it
    return Registry, Registry.load(home / "registry.json")


def cmd_init(ns):
    zone = ns.init_zone or ns.zone or "personal"
    if ns.mode == "hybrid":
        raise GatewayError("not_implemented", "mode hybrid is not implemented in 0.1.0",
                           hints=["use mode local, hub or client"])
    home = _home(ns, zone)
    gw = home / "gateway.json"
    if gw.exists():
        raise GatewayError("already_initialized", f"{gw} already exists")
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    cfg = {
        "schema": 1, "zone": zone, "mode": ns.mode, "socket": None,
        "listen": {"host": "127.0.0.1", "port": 8765, "tls_cert": None, "tls_key": None,
                   "mcp_http": False, "mcp_http_trust_loopback": False},
        "hub": {"url": None, "client_id": None, "credential_ref": None, "timeout_s": 8},
        "local_connections": [], "offline": "fail-closed",
        "limits": {"max_result_chars": 24000, "default_page": 20},
    }
    write_json(gw, cfg)
    reg = home / "registry.json"
    wrote = [str(gw)]
    if not reg.exists():
        write_json(reg, {"schema": 1, "zone": zone, "connections": [], "clients": []})
        wrote.append(str(reg))
    out = {"ok": True, "home": str(home), "zone": zone, "mode": ns.mode, "wrote": wrote}
    if ns.mode == "hub":
        out["next"] = ("a plain MCP client (any MCP-over-HTTP connector) can use this hub "
                       "at POST /mcp: set listen.mcp_http to true in " + str(gw) +
                       ", enroll the client (lotr enroll), restart lotrd")
    return out


def _load_template(ns, home, want_kind):
    """The template named by --from-template, or None. A wrong kind says which command to use."""
    name = getattr(ns, "from_template", None)
    if not name:
        return None
    from lotrlib import conn_templates
    tpl = conn_templates.get_template(home, name)
    if tpl["kind"] != want_kind:
        use = {"http": "add-http", "mcp-stdio": "add-stdio", "mcp-oauth": "connect",
               "mcp-http": "add-mcp"}[tpl["kind"]]
        raise GatewayError("template_kind_mismatch",
                           f"template {name!r} is kind {tpl['kind']}, not {want_kind}",
                           hints=[f"use: lotr {use} ... --from-template {name}"])
    return tpl


def cmd_add_http(ns):
    home = _admin_home(ns)
    _, reg = _registry(home)
    tpl = _load_template(ns, home, "http")
    pol = {"deny": [], "consent": [], "write": [], "read": []}
    if tpl:
        c = tpl["connect"]
        ns.base_url = ns.base_url or c["base_url"]
        ns.profile = ns.profile or c.get("profile", "generic")
        ns.auth = ns.auth or c["auth_scheme"]
        ns.token_ref = ns.token_ref or c.get("credential_ref")
        ns.user = ns.user or c.get("auth_user")
        ns.header = ns.header or c.get("auth_header")
        ns.identity = ns.identity or c.get("identity")
        ns.description = ns.description or c.get("description")
        ns.trust = ns.trust or c.get("trust")
        pol.update({k: list(v) for k, v in (c.get("policy") or {}).items()})
    for flag, val in (("--profile", ns.profile), ("--base-url", ns.base_url),
                      ("--identity", ns.identity), ("--auth", ns.auth)):
        if not val:
            raise _Usage(f"add-http needs {flag} (or --from-template NAME that supplies it)")
    if "TODO" in " ".join(str(x) for x in (ns.identity, ns.description, ns.base_url)):
        raise _Usage("the template still has TODO values: run lotr template check NAME")
    ns.trust = ns.trust or "T0"
    if ns.auth != "none" and not ns.token_ref:
        raise _Usage(f"--auth {ns.auth} needs --token-ref")
    host = urlparse(ns.base_url).hostname
    entry = {
        "id": ns.id, "identity": ns.identity, "zone": reg.zone, "kind": "http",
        "profile": ns.profile, "description": ns.description or "",
        "base_url": ns.base_url,
        "auth": {"scheme": ns.auth, "token_ref": ns.token_ref, "user": ns.user,
                 "header": ns.header},
        "headers": {}, "network": {"hosts": [host] if host else []},
        "trust": ns.trust, "policy": pol,
        "enabled": True,
    }
    reg.add_connection(entry)
    reg.save(home / "registry.json")
    return {"ok": True, "added": ns.id}


def _parse_params(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise _Usage(f"--param expects NAME=VALUE, got {p.split('=')[0]!r}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def cmd_add_stdio(ns):
    """Register an MCP server that speaks stdio (0.5.0): LOTR launches `--command` (argv, no shell) on
    first use. Secrets reach the process only as env REFS (--env NAME=REF), never as values."""
    from lotrlib.conn_stdio import McpStdioConnection
    from lotrlib import conn_stdio
    from lotrlib.registry import validate_connection
    home = _admin_home(ns)
    _, reg = _registry(home)
    tpl = _load_template(ns, home, "mcp-stdio")
    env, cmd, cwd = {}, None, ns.cwd
    plain, runtime, bridge, tparams = {}, None, None, {}
    pol = {"deny": [], "consent": [], "write": [], "read": []}
    timeouts = {}
    if tpl:
        c = tpl["connect"]
        cmd = list(c["command"])
        env.update(c.get("env_refs") or {})
        plain.update(c.get("env") or {})
        runtime, bridge = c.get("runtime"), c.get("bridge")
        tparams = c.get("params") or {}
        cwd = cwd or c.get("cwd")
        ns.identity = ns.identity or c.get("identity")
        ns.description = ns.description or c.get("description")
        ns.trust = ns.trust or c.get("trust")
        timeouts = dict(c.get("timeouts") or {})
        pol.update({k: list(v) for k, v in (c.get("policy") or {}).items()})
    if tparams:
        from lotrlib import conn_templates
        pv = _parse_params(getattr(ns, "param", None))
        cmd = conn_templates.apply_params(cmd, tparams, pv)
        plain = conn_templates.apply_env_params(plain, tparams, pv)     # 0.5.0: {name} in env values
    if ns.command_line:
        cmd = _split_cmd(ns.command_line)
    if not cmd:
        raise _Usage("add-stdio needs --command \"PROGRAM ARGS\" (or --from-template NAME that supplies it)")
    if not ns.identity:
        raise _Usage("add-stdio needs --identity (or --from-template NAME that supplies it)")
    if any("TODO" in x for x in cmd + [ns.identity]):
        raise _Usage("the template still has TODO values: run lotr template check NAME")
    for pair in ns.env or []:
        if "=" not in pair:
            raise _Usage(f"--env expects NAME=REF (a secret reference), got {pair.split('=')[0]!r}")
        k, v = pair.split("=", 1)
        env[k] = v
    for pair in getattr(ns, "env_plain", None) or []:
        if "=" not in pair:
            raise _Usage(f"--env-plain expects NAME=VALUE, got {pair.split('=')[0]!r}")
        k, v = pair.split("=", 1)
        plain[k] = v
    if getattr(ns, "runtime", None):
        runtime = ns.runtime
    from lotrlib import runtimes
    runtimes.require(runtime, cmd)             # a missing npx / uvx / docker is explained, not a crash
    entry = {
        "id": ns.id, "identity": ns.identity, "zone": reg.zone, "kind": "mcp",
        "profile": "mcp", "description": ns.description or "", "transport": "stdio",
        "command": cmd, "env": env, "auth": {"scheme": "none"},
        "network": {"hosts": []}, "trust": ns.trust or "T1", "policy": pol, "tools": [],
        "enabled": True,
    }
    if plain:
        entry["env_plain"] = plain
    if runtime:
        entry["runtime"] = runtime
    if bridge:
        entry["bridge"] = bridge
    if cwd:
        entry["cwd"] = str(Path(cwd).expanduser().resolve())
    if timeouts:
        entry["timeouts"] = timeouts
    validate_connection(entry, reg.zone)       # before the command ever runs: a literal secret stops here
    try:
        entry["tools"] = McpStdioConnection(entry, None).discover()
    finally:
        conn_stdio.forget(entry["id"])         # the CLI is a one-shot: no process outlives it
    reg.add_connection(entry)
    reg.save(home / "registry.json")
    out = {"ok": True, "added": ns.id, "tools": len(entry["tools"])}
    if bridge:
        out["warning"] = ("this is a bridge: it keeps its own sign-in token outside LOTR's secrets "
                          "store, where LOTR cannot seal, rotate or revoke it")
    return out


def cmd_template(ns):
    from lotrlib import template_tool
    home = _home(ns)
    if ns.tcmd == "guide":
        return _Text(template_tool.guide_text())
    if ns.tcmd == "new":
        return template_tool.write_new(home, ns.name, ns.kind, doc=ns.from_doc, out=ns.out,
                                       force=ns.force, archetype=ns.archetype)
    if ns.tcmd == "check":
        r = template_tool.check_file(ns.file, home)
        return r
    if ns.tcmd == "facts-check":
        return template_tool.facts_check(ns.file, home, template=ns.template)
    if ns.tcmd == "update":
        from lotrlib import catalog
        _admin_home(ns)                       # a catalog is installed into an initialised home
        return catalog.update(home, base=ns.url, ref=ns.ref, dry_run=ns.dry_run, force=ns.force,
                              only=ns.only or None,
                              allow_insecure_localhost=ns.allow_insecure_localhost)
    out = template_tool.list_templates(home)
    from lotrlib import persona
    sug = persona.suggestion([t.get("name") for t in out["templates"]])
    if sug:                                   # only when gt has a persona that suggests some
        out["suggested"] = sug
    return out


class _Report(_Text):
    """`lotr setup`'s plain-words report; .rc is the exit code (0 done, 3 needs you, 1 failed)."""
    rc = 0


def _person_can_type():
    from lotrlib import setup_run
    return setup_run.person_terminal()


def _prompt_secret(label):
    import getpass
    value = getpass.getpass(f"{label} (not shown, Enter to skip): ").strip()
    return value or None


def _put_store(ref, value):
    from lotrlib import oauth
    scheme, _, name = ref.partition(":")
    return oauth.write_store_secret(name, value)


def cmd_secret(ns):
    """`lotr secret put NAME` -- the value is typed at a hidden prompt in a person's own terminal
    and goes straight to the store file; it is never an argument, never printed, never in Claude."""
    from lotrlib import secrets as secrets_mod
    if ns.scmd == "check":
        d = secrets_mod.describe(ns.ref)
        return {"ok": True, "ref": ns.ref, "scheme": d.get("scheme"), "present": d.get("present")}
    if not _person_can_type():
        raise GatewayError("secret_needs_terminal",
                           "a secret is typed by you, in your own terminal, not through Claude",
                           hints=["open a terminal of your own and run: lotr secret put " + ns.name])
    if not secrets_mod._STORE_NAME.match(ns.name or ""):
        raise GatewayError("secret_ref_invalid", "a secret name is one plain word: letters, digits, . _ -")
    ref = "store:" + ns.name
    if secrets_mod.describe(ref).get("present") and not ns.force:
        raise GatewayError("secret_exists", f"{ref} already holds a value",
                           hints=["pass --force to replace it"])
    value = _prompt_secret(f"Value for {ref}")
    if not value:
        raise GatewayError("secret_empty", "nothing was typed; nothing was stored")
    _put_store(ref, value)
    return {"ok": True, "stored": ref, "note": "the value was not shown and is not logged"}


def _setup_argv_ns(argv, outer):
    """A sub-command's namespace, built through the real parser so its validation is the one that
    runs; the gateway home of the outer command carries over."""
    ns2, extra = build_parser().parse_known_args(argv)
    if extra:
        raise _Usage("internal: setup built an unusable command line")
    ns2.home, ns2.zone = outer.home, outer.zone
    return ns2


def cmd_setup(ns):
    """The execution run: check the template and the runtime, make sure the credential is where the
    template says (or say exactly where to put it, or sign the user in), add the connection, smoke
    test it, and say the result in plain words. Safe to run again: a connection that exists is not
    added twice, it is tested."""
    from lotrlib import conn_templates, runtimes, setup_run, template_tool
    home = _admin_home(ns)
    if ns.template is None:                   # only reachable with a persona's suggestions
        return _finish_setup(ns, _setup_chooser(home))
    _, reg = _registry(home)
    try:
        tpl = conn_templates.get_template(home, ns.template)
    except GatewayError as e:
        if e.code != "unknown_template":
            raise
        rep = setup_run.Report(f"Setting up {ns.template}")
        rep.stop("template_not_ready", "template", e.message,
                 [*e.hints, *setup_run.builtin_hints(ns.template), "see them all: lotr template list"])
        return _finish_setup(ns, rep)
    name, kind = tpl["name"], tpl["kind"]
    ns.name = ns.id or name
    cid = _conn_id(ns, reg)
    rep = setup_run.Report(f"Setting up {cid} from the {name} template ({kind})")
    chk = template_tool.check_file(name, home)
    if not chk["ok"]:
        rep.stop("template_not_ready", "template",
                 "the template is not finished:\n" + "\n".join(chk["problems"][:8]),
                 [f"fix them (vim {chk['file']}), then run: lotr template check {name}"])
        return _finish_setup(ns, rep)
    rep.ok("template", "checked: valid, nothing left to fill in")
    for w in chk.get("warnings", []):
        if w.startswith("BRIDGE"):
            rep.warn("bridge", w)
    bridge_note = any(w.startswith("BRIDGE") for w in chk.get("warnings", []))
    c = tpl["connect"]
    exists = cid in reg.connections
    ident = setup_run.identity_for(tpl, ns.identity)
    pending = []                                   # [(label, ref)] still to be put in place
    if exists:
        rep.ok("connection", f"{cid} already exists, so it is not added again; it will be tested")
    else:
        if kind == "mcp-stdio":
            rt = runtimes.check(c.get("runtime"), c["command"], deep=True)
            if not rt["ok"]:
                rep.stop("needs_runtime", "runtime", rt["say"], [rt["fix"], f"then run again: lotr setup {name}"])
                return _finish_setup(ns, rep)
            rep.ok("runtime", f"{rt['program']} found at {rt['found']}")
            cmd_final = list(c["command"])
            if c.get("params"):
                vals = _parse_params(ns.param)
                for k, spec in c["params"].items():
                    if k not in vals and "default" not in spec and _person_can_type() and not ns.no_prompt:
                        sys.stderr.write(f"{spec['prompt']}: ")
                        sys.stderr.flush()
                        got = sys.stdin.readline().strip()
                        if got:
                            vals[k] = got
                missing_p = [k for k, sp in c["params"].items() if k not in vals and "default" not in sp]
                if missing_p:
                    rep.stop("needs_input", "input", "\n".join(
                        f"{k}: {c['params'][k]['prompt']}" for k in missing_p),
                        ["run again with: lotr setup " + name + "".join(f" --param {k}=VALUE" for k in missing_p)])
                    return _finish_setup(ns, rep)
                try:
                    cmd_final = conn_templates.apply_params(c["command"], c["params"], vals)
                except GatewayError as e:
                    rep.stop("needs_input", "input", f"{e.message}", list(e.hints or []))
                    return _finish_setup(ns, rep)
            line = " ".join(cmd_final)
            rep.ok("will run", f"{line}\non this machine, started by LOTR when the connection is used"
                   + ("\nThis bridge keeps its own token outside LOTR's store (see the note above)."
                      if bridge_note else ""))
            if not (ns.yes or (_person_can_type() and _ask_yes("Run that program on this machine? [y/N] "))):
                rep.stop("needs_confirmation", "confirm",
                         "setup will not run a program until you say yes",
                         [f"read the command above; if it is what you expect, run: lotr setup {name}"
                          + (f" --id {cid}" if ns.id else "") + " --yes"])
                return _finish_setup(ns, rep)
            for var, ref in (c.get("env_refs") or {}).items():
                pending.append((f"{var} for {name}", ref))
        elif kind in ("http", "mcp-http"):
            ref = ns.token_ref or c.get("credential_ref")
            if c.get("auth_scheme") != "none":
                if not ref:
                    rep.stop("template_not_ready", "credential", "the template names no credential_ref",
                             [f"run again with --token-ref store:{name}-key"])
                    return _finish_setup(ns, rep)
                pending.append((f"the credential for {name}", ref))
        elif kind == "mcp-oauth":
            if c.get("client_registration") == "preregistered":
                nm = cid.split("@")[0]
                if not ns.client_id_ref:
                    ns.client_id_ref = f"store:{nm}-client-id"
                pending.append((f"the OAuth app's client id for {name}", ns.client_id_ref))
                if c.get("client_secret_required"):
                    if not ns.client_secret_ref:
                        ns.client_secret_ref = f"store:{nm}-client-secret"
                    pending.append((f"the OAuth app's client secret for {name}", ns.client_secret_ref))
                if c.get("app_setup_url"):
                    rep.ok("app", f"this server needs an app you create: {c['app_setup_url']}\n"
                           f"register the redirect address http://{c.get('redirect_host', '127.0.0.1')}:"
                           f"{c.get('redirect_port', 'PORT')}/callback")
    missing = []
    for label, ref in pending:
        st = setup_run.secret_state(ref)
        if st != "missing":
            rep.ok("credential", f"{label}: found at {ref}" if st == "present" else
                   f"{label}: {ref} (kept by a secrets tool; checked when it is used)")
            continue
        scheme = ref.partition(":")[0]
        if _person_can_type() and scheme == "store" and not ns.no_prompt:
            value = _prompt_secret(f"Paste {label}")
            if value:
                _put_store(ref, value)
                value = None
                rep.ok("credential", f"{label}: stored at {ref} (it was not shown or logged)")
                continue
        missing.append((label, ref))
    if missing:
        lines = []
        for label, ref in missing:
            lines += setup_run.put_lines(ref, label)
        rep.stop("needs_credential", "credential", "\n".join(lines[:1]),
                 [x for x in lines[1:]] + [f"then run: lotr setup {name}" + (f" --id {cid}" if ns.id else "")])
        return _finish_setup(ns, rep)
    if not exists:
        base = [cid, "--from-template", name]
        try:
            if kind == "http":
                a = _setup_argv_ns(["add-http", *base, "--identity", ident]
                                   + (["--token-ref", ns.token_ref] if ns.token_ref else []), ns)
                cmd_add_http(a)
            elif kind == "mcp-http":
                a = _setup_argv_ns(["add-mcp", *base, "--identity", ident]
                                   + (["--auth-ref", ns.token_ref] if ns.token_ref else []), ns)
                cmd_add_mcp(a)
            elif kind == "mcp-stdio":
                a = _setup_argv_ns(["add-stdio", *base, "--identity", ident]
                                   + [x for k, v in _parse_params(ns.param).items()
                                      for x in ("--param", f"{k}={v}")], ns)
                cmd_add_stdio(a)
            else:
                argv = ["connect", cid, "--from-template", name, "--identity", ident]
                if ns.yes:
                    argv.append("--yes")
                if ns.client_id_ref:
                    argv += ["--client-id-ref", ns.client_id_ref]
                if ns.client_secret_ref:
                    argv += ["--client-secret-ref", ns.client_secret_ref]
                if not _person_can_type():
                    raise GatewayError("oauth_not_a_terminal", "the sign-in must be started by you")
                cmd_connect(_setup_argv_ns(argv, ns))
        except GatewayError as e:
            if e.code == "oauth_not_a_terminal":
                rep.stop("needs_terminal", "sign-in", "signing in is something you do, in your own terminal",
                         [f"open a terminal of your own and run: lotr setup {name}"
                          + (f" --id {cid}" if ns.id else ""),
                          "it opens the sign-in page, then tests the connection"])
            else:
                rep.stop("failed", "connection", f"{e.code}: {e.message}", list(e.hints or []))
            return _finish_setup(ns, rep)
        rep.ok("connection", f"{cid} added")
    if ns.no_smoke:
        rep.warn("test", "skipped (--no-smoke)")
        return _finish_setup(ns, rep)
    try:
        sm = setup_run.smoke(home, cid, tpl)
    except GatewayError as e:
        rep.stop("failed", "test", f"{e.code}: {e.message}", list(e.hints or []))
        return _finish_setup(ns, rep)
    rep.ok("test", "\n".join(sm["lines"])) if sm["ok"] else rep.stop(
        "failed", "test", "\n".join(sm["lines"]),
        [f"look at it again with: lotr status --check --table", f"or run again after fixing: lotr setup {name}"
         + (f" --id {cid}" if ns.id else "")])
    if sm["ok"]:
        rep.after.append(f"use it: lotr find \"<what you want>\" --connection {cid}  (or just ask Claude)")
    return _finish_setup(ns, rep)


def _ask_yes(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _setup_chooser(home):
    """`lotr setup` with no template, when gt names a persona that suggests templates (0.5.0): the
    suggestions, then every template here, in lines that never wrap. It sets nothing up."""
    from lotrlib import persona, setup_run, template_tool
    from lotrlib.util import wrap_list
    names = sorted({t.get("name") for t in template_tool.list_templates(home)["templates"]
                    if t.get("name")})
    rep = setup_run.Report("Setting up a service")
    rep.stop("needs_input", "template", "no template was named; choose one")
    rep.after += persona.suggest_lines(names)
    rep.after += wrap_list("available: ", names) if names else ["available: (none)"]
    rep.after += ["then run: lotr setup NAME", "see them all: lotr template list"]
    return rep


def _setup_template_optional(argv):
    """True only for `lotr setup ...` when gt's persona suggests templates: then a missing template
    shows the chooser instead of the usage error. Without a persona the parser is unchanged."""
    if "setup" not in (argv or []):
        return False
    try:
        from lotrlib import persona
        return bool(persona.suggested()[1])
    except Exception:                         # noqa: BLE001 -- no suggestion, no change
        return False


def _finish_setup(ns, rep):
    if ns.json:
        d = rep.to_dict()
        d["ok"] = rep.status == "done"
        return d
    t = _Report(rep.render())
    t.rc = 0 if rep.status == "done" else (3 if rep.status.startswith("needs_") else 1)
    return t


def _split_cmd(text):
    """A command line -> argv. POSIX shell quoting, or on native Windows the Windows rules
    (backslashes are path separators there, and paths are double-quoted) -- 0.3.0."""
    import shlex
    if os.name != "nt":
        return shlex.split(text)
    out = []
    for tok in shlex.split(text, posix=False):
        # a whole argument in "double" or 'single' quotes (pull.join_command uses single quotes
        # so the line can itself sit inside a double-quoted --command argument)
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def cmd_add_mcp(ns):
    """Register an SSO/OAuth-protected MCP endpoint (0.2.0): the token by REF, refreshed by the
    MCP client's own helper; its tools discovered now and cached, so find works at once.
    0.5.0: `--from-template T` (kind mcp-http) registers a hosted MCP server with a static key,
    sent as `Authorization: Bearer` or in a named header; the key is a REFERENCE."""
    from lotrlib.conn_mcp import McpConnection
    from lotrlib.registry import validate_connection
    home = _admin_home(ns)
    _, reg = _registry(home)
    tpl = _load_template(ns, home, "mcp-http")
    pol = {"deny": [], "consent": [], "write": [], "read": []}
    scheme_t = None
    if tpl:
        c = tpl["connect"]
        ns.endpoint = ns.endpoint or c["url"]
        ns.identity = ns.identity or c.get("identity")
        ns.description = ns.description or c.get("description")
        ns.trust = ns.trust or c.get("trust")
        ns.auth_ref = ns.auth_ref or c.get("credential_ref")
        ns.auth_header = ns.auth_header or c.get("auth_header")
        ns.auth_prefix = getattr(ns, "auth_prefix", None) or c.get("auth_prefix")
        scheme_t = c["auth_scheme"]
        pol.update({k: list(v) for k, v in (c.get("policy") or {}).items()})
    if not ns.endpoint:
        raise _Usage("add-mcp needs --endpoint (or --from-template NAME that supplies it)")
    if not ns.identity:
        raise _Usage("add-mcp needs --identity (or --from-template NAME that supplies it)")
    if "TODO" in " ".join(str(x) for x in (ns.identity, ns.description, ns.endpoint)):
        raise _Usage("the template still has TODO values: run lotr template check NAME")
    if scheme_t == "none":
        ns.auth_ref = None
    if scheme_t in ("bearer", "header", "prefix") and not ns.auth_ref:
        raise _Usage(f"auth_scheme {scheme_t} needs --auth-ref (a reference to the key)")
    host = urlparse(ns.endpoint).hostname
    scheme = "none"
    if ns.auth_ref:
        scheme = "header" if (ns.auth_header or scheme_t == "header") else "bearer"
    auth = {"scheme": scheme, "token_ref": ns.auth_ref}
    if scheme == "header":
        auth["header"] = ns.auth_header
    elif scheme == "bearer" and getattr(ns, "auth_prefix", None):
        auth["prefix"] = ns.auth_prefix          # 0.5.0: Authorization: <prefix><key>
    entry = {
        "id": ns.id, "identity": ns.identity, "zone": reg.zone, "kind": "mcp",
        "profile": "mcp", "description": ns.description or "",
        "endpoint": ns.endpoint, "transport": ns.transport,
        "auth": auth,
        "refresh_cmd": _split_cmd(ns.refresh_cmd) if ns.refresh_cmd else None,
        "network": {"hosts": [host] if host else []}, "trust": ns.trust or "T1",
        "policy": pol, "tools": [],
        "enabled": True,
    }
    validate_connection(entry, reg.zone)       # before any network: a literal token stops here
    entry["tools"] = McpConnection(entry, None, secret_resolver=_read_ref).discover()
    reg.add_connection(entry)
    reg.save(home / "registry.json")
    return {"ok": True, "added": ns.id, "tools": len(entry["tools"])}


# ------------------------------------------------------------------ pull (0.5.0)

def cmd_pull(ns):
    """`lotr pull`: list the MCP servers this machine's Claude Code already has configured and the
    command that brings each into LOTR. Read-only. `--import NAME` registers a server whose
    token a client already keeps in a private file, BY REFERENCE to that file, after a yes."""
    from lotrlib import pull
    if ns.yes and not ns.import_names:
        raise _Usage("--yes goes with --import NAME: pull never registers anything on its own")
    home = _admin_home(ns)
    _, reg = _registry(home)
    prefix = pull.command_prefix(ns.home, ns.zone)
    report = pull.scan(home, reg, extra_files=ns.from_file, probe=not ns.no_probe,
                       allow_private=ns.allow_private_network, prefix=prefix)
    if not ns.import_names:
        return _Text(pull.render_table(report)) if ns.table else report
    chosen = pull.select(report, ns.import_names)
    plan = [pull.plan_of(c) for c in chosen]
    if ns.dry_run:
        return {"ok": True, "dry_run": True, "would_register": plan}
    _say("About to register by reference (no token is copied or shown):\n" + "".join(
        f"  {p['name']}  ->  {p['id']}\n    endpoint  {p['endpoint']}\n"
        f"    token     {p['ref']}  (LOTR reads this file when it calls; the file stays where it is)\n"
        f"              (found by the server's name alone: is it that server's token?)\n"
        for p in plan))
    if not ns.yes:
        if not has_terminal(sys.stdin):
            raise GatewayError("import_needs_confirmation", "no terminal to confirm at; run this in "
                               "your own terminal, or pass --yes after reading the summary above")
        if not _ask_yes("Register these connections? [y/N] "):
            raise GatewayError("import_declined", "not confirmed; nothing was registered")
    registered, failed = [], []
    for c, p in zip(chosen, plan):
        try:
            a = _setup_argv_ns(["add-mcp", p["id"], "--endpoint", p["endpoint"], "--identity",
                                p["identity"], "--auth-ref", p["ref"]], ns)
            r = cmd_add_mcp(a)
            registered.append({"name": p["name"], "id": p["id"], "tools": r["tools"]})
        except GatewayError as e:
            failed.append({"name": p["name"], "id": p["id"], "error": e.to_dict()})
    return {"ok": not failed, "registered": registered, "failed": failed}


# ------------------------------------------------------------------ OAuth (0.3.0)

def _say(msg):
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.stderr.flush()


def _conn_id(ns, reg):
    """The connection id a verb was given, checked BEFORE anything else happens (no network, no
    prompt, no file): the registry's own id rule, this registry's zone, a sane length."""
    from lotrlib.registry import CONN_ID
    cid = ns.name if "@" in ns.name else "%s@%s" % (ns.name, reg.zone)
    if len(cid) > 100 or not CONN_ID.match(cid) or ".." in cid:
        raise GatewayError("bad_connection_name", "a connection name is lowercase letters, digits, "
                           ". _ - (starting with a letter or digit), optionally @zone, at most 100 "
                           "characters")
    if cid.split("@", 1)[1] != reg.zone:
        raise GatewayError("bad_connection_name", f"this gateway's zone is {reg.zone}; a connection "
                           "of another zone cannot be registered here")
    return cid


def _read_secret_file(path):
    from lotrlib.secrets import _read_file
    return _read_file("file:" + str(path), Path(path))


def _confirm(ns):
    """The visible consent step of connect/login: show what the server will be asked for and wait
    for a yes at THIS terminal (`--yes` skips the wait for scripted use; the summary still prints)."""
    def ask(info):
        _say("About to sign in to:\n"
             f"  server      {info['resource']}\n"
             f"  hosts       {', '.join(info['hosts'])} (pinned: LOTR will only talk to these)\n"
             f"  issuer      {info['issuer']}\n"
             f"  permissions {' '.join(info['scopes']) or '(none requested: the server decides)'}\n"
             f"  client      {info['client']}\n"
             + "".join(f"  note        {n}\n" for n in info["notes"]))
        if getattr(ns, "yes", False):
            return True
        if not has_terminal(sys.stdin):
            raise GatewayError("oauth_needs_confirmation", "no terminal to confirm at; run this in "
                               "your own terminal, or pass --yes after reading the summary above")
        sys.stderr.write("Open the browser and sign in? [y/N] ")
        sys.stderr.flush()
        return sys.stdin.readline().strip().lower() in ("y", "yes")
    return ask


def _client_secret_from(ns):
    if getattr(ns, "client_secret_value", None):          # 0.5.0: read from a reference (setup)
        return ns.client_secret_value
    if getattr(ns, "client_secret_ref", None):
        return _read_ref(ns.client_secret_ref).strip() or None
    if getattr(ns, "client_secret_file", None):
        return _read_secret_file(Path(ns.client_secret_file).expanduser())
    if getattr(ns, "client_secret_prompt", False):
        import getpass
        if not has_terminal(sys.stdin):
            raise _Usage("--client-secret-prompt needs a terminal")
        value = getpass.getpass("client secret (not echoed): ").strip()
        if not value:
            raise _Usage("no client secret was typed")
        return value
    if getattr(ns, "client_secret_stdin", False):
        data = sys.stdin.read().strip()
        if not data:
            raise _Usage("--client-secret-stdin read nothing")
        return data
    return None


def _store_secret(name, value, sealed):
    """-> the ref. sealed: through the authority (a person is at this terminal, so a factor may
    be asked of them); else an L1 store file."""
    from lotrlib import oauth, unlock
    if not sealed:
        return oauth.write_store_secret(name, value)
    unlock.call("seal_put", {"name": name, "value": value, "tty": True},
                answer=unlock.tty_answer())
    return "sealed:" + name


def _drop_secret(ref):
    """Delete what a ref names (only the two kinds LOTR writes). -> True / False."""
    from lotrlib import oauth, unlock
    scheme, _, name = (ref or "").partition(":")
    try:
        if scheme == "store":
            return oauth.delete_store_secret(name)
        if scheme == "sealed":
            unlock.call("seal_rm", {"name": name, "tty": True}, answer=unlock.tty_answer())
            return True
    except GatewayError:
        return False
    return False


def _read_ref(ref):
    """Read a ref in THIS terminal; a sealed one is asked of the authority with the terminal as
    the place a code can be typed."""
    from lotrlib import secrets as secrets_mod, unlock
    if secrets_mod.brokered(ref):
        res = unlock.call("secret", {"ref": ref, "tty": True}, answer=unlock.tty_answer())
        value = res.get("value") if isinstance(res, dict) else None
        if not isinstance(value, str) or not value:
            raise GatewayError("secret_missing", f"secret {ref} resolved to nothing")
        return value
    return secrets_mod.resolve(ref)


def _level(ref):
    if not ref:
        return "none (the server issued no refresh token)"
    if ref.startswith("sealed:"):
        return "L2 (sealed by gt unlock)"
    if os.name == "nt":
        return ("L1 (a file in your profile directory, protected by its ACL, not by mode bits; "
                "any process of this user can read it)")
    return "L1 (a mode-600 file; any process of this user can read it)"


def _finish_login(ns, reg, home, cid, res, existing=None):
    """Persist what a login returned and build the registry entry (the caller saves it).
    -> (entry, old_refs_to_drop). Nothing is written to the registry here; secrets are written
    only after the entry validated, and are removed again if the tool listing then fails on a
    NEW connection."""
    from lotrlib import oauth, unlock
    from lotrlib.conn_mcp import McpConnection
    from lotrlib.registry import validate_connection
    block, tokens = res["block"], res["tokens"]
    rt, at, exp = tokens["refresh_token"], tokens["access_token"], tokens["expires_at"]
    tokens["refresh_token"] = tokens["access_token"] = None
    old = (existing or {}).get("auth") or {}
    old_refs = [r for r in (old.get("token_ref"),
                            (old.get("oauth") or {}).get("client_secret_ref")) if r]
    sealed = (unlock.enabled() and not getattr(ns, "l1", False)) or \
        (existing is not None and (old.get("token_ref") or "").startswith("sealed:"))
    prefix = "sealed:" if sealed else "store:"
    rt_name, cs_name = oauth.secret_name(cid), oauth.secret_name(cid, "client")
    rt_ref = prefix + rt_name if rt else None
    cs_ref = prefix + cs_name if res["client_secret"] else None
    block["client_secret_ref"] = cs_ref
    ep = (existing or {}).get("endpoint") or ns.url
    host = urlparse(ep).hostname
    entry = existing or {
        "id": cid, "identity": ns.identity or ("oauth @ %s" % host), "zone": reg.zone,
        "kind": "mcp", "profile": "mcp", "description": getattr(ns, "description", None) or "",
        "endpoint": ep, "transport": "http", "refresh_cmd": None, "trust": ns.trust,
        "policy": {"deny": [], "consent": [], "write": [], "read": []}, "tools": [],
        "enabled": True}
    entry["network"] = {"hosts": sorted(set([host] + list(res["hosts"])))}
    entry["auth"] = {"scheme": "oauth", "token_ref": rt_ref, "oauth": block}
    validate_connection(entry, reg.zone)           # before any secret is written
    written = []
    try:
        if rt:
            _store_secret(rt_name, oauth.wrap_secret(block, rt_ref, rt), sealed)
            written.append(rt_ref)
        if res["client_secret"]:
            _store_secret(cs_name, oauth.wrap_secret(block, cs_ref, res["client_secret"]), sealed)
            written.append(cs_ref)
        rt = None
        oauth.forget(cid)
        oauth.seed_access(entry, "-", at, exp)
        at = None
        try:
            entry["tools"] = McpConnection(entry, None, secret_resolver=_read_ref).discover()
        except GatewayError:
            if existing is None:
                raise                               # a connection that lists nothing is no use
    except BaseException:
        if existing is None:
            for r in written:
                _drop_secret(r)
        raise
    finally:
        oauth.forget(cid)
    return entry, [r for r in old_refs if r not in (rt_ref, cs_ref)]


def _apply_connect_template(ns, home):
    """If --from-template was given, pre-fill ns from the template; an explicit CLI flag wins.

    Returns the template's declared pinned hosts (a list) so the caller can assert the
    discovered pin set does not exceed it, or None when no template was used."""
    name = getattr(ns, "from_template", None)
    if not name:
        return None
    from lotrlib import conn_templates
    tpl = conn_templates.get_template(home, name)
    if tpl["kind"] != "mcp-oauth":
        use = {"http": "add-http", "mcp-stdio": "add-stdio", "mcp-http": "add-mcp"}[tpl["kind"]]
        raise GatewayError("template_kind_mismatch",
                           f"template {name!r} is kind {tpl['kind']}, not mcp-oauth",
                           hints=[f"use: lotr {use} ... --from-template {name}"])
    c = tpl["connect"]
    # scalar prefills: fill only where the user did not pass the flag (CLI wins)
    if not ns.url:
        ns.url = c.get("url")
    if not ns.scope and c.get("scopes"):
        ns.scope = list(c["scopes"])
    if not ns.offline_access and c.get("offline_access"):
        ns.offline_access = True
    if not ns.skip_audience_check and c.get("skip_audience_check"):
        ns.skip_audience_check = True
    # send_resource:false in the template means the same as the --no-resource flag
    if not ns.no_resource and c.get("send_resource") is False:
        ns.no_resource = True
    if ns.redirect_host == "127.0.0.1" and c.get("redirect_host"):
        ns.redirect_host = c["redirect_host"]
    if ns.trust == "T1" and c.get("trust"):
        ns.trust = c["trust"]
    if not ns.identity and c.get("identity"):
        ns.identity = c["identity"]
    if not ns.description and c.get("description"):
        ns.description = c["description"]
    # 0.5.0: a server whose OAuth app the user creates (client_registration preregistered)
    ns.prereg = c.get("client_registration") == "preregistered"
    ns.prereg_secret = bool(c.get("client_secret_required"))
    ns.app_setup_url = c.get("app_setup_url")
    ns.tpl_name = name
    ns.tpl_policy = {k: list(v) for k, v in (c.get("policy") or {}).items()}
    ns.auth_params = dict(c.get("auth_params") or {})        # 0.5.0: allow-listed, no secret
    if not ns.redirect_port and c.get("redirect_port"):
        ns.redirect_port = c["redirect_port"]
    return list((tpl.get("network") or {}).get("hosts") or []) or None


def _metadata_hosts(ns):
    """`--allow-insecure-metadata HOST` (0.5.0) -> the checked, sorted host list. A name that is not
    a bare host is a usage error, before any network, any prompt or any file."""
    from lotrlib import oauth
    try:
        return oauth.parse_metadata_hosts(getattr(ns, "allow_insecure_metadata", None))
    except GatewayError as e:
        raise _Usage(e.message)


def cmd_connect(ns):
    from lotrlib import oauth
    home = _admin_home(ns)
    _, reg = _registry(home)
    tpl_hosts = _apply_connect_template(ns, home)   # pre-fill ns from --from-template; CLI wins
    cid = _conn_id(ns, reg)
    meta_hosts = _metadata_hosts(ns)
    _own_terminal_gate(f"connect {cid}")
    if cid in reg.connections:
        raise GatewayError("duplicate_connection", f"connection {cid} already exists",
                           hints=[f"re-authenticate it with: lotr login {cid}",
                                  f"or remove it with: lotr disconnect {cid}"])
    if not ns.url:
        raise GatewayError("missing_url", "connect needs --url, or --from-template NAME "
                                          "that supplies one")
    client_id = ns.client_id
    if getattr(ns, "client_id_ref", None):                # 0.5.0: the id as a reference, too
        client_id = _read_ref(ns.client_id_ref).strip() or None
    client_secret = _client_secret_from(ns)
    if getattr(ns, "prereg", False):
        # the template says this server's app is created by the user: its id (and secret) are needed
        # before any sign-in starts, and each is a reference, never a value in a file or on a command line
        nm = ns.name.split("@")[0]
        if not client_id:
            raise GatewayError(
                "client_id_needed", "this server needs an OAuth app that you create first, and "
                "its client id was not given",
                hints=([f"create the app at {ns.app_setup_url}"] if ns.app_setup_url else []) + [
                    f"redirect address to register: http://{ns.redirect_host}:{ns.redirect_port or 'PORT'}/callback",
                    f"put the client id where LOTR can read it: lotr secret put {nm}-client-id",
                    f"then: lotr connect {ns.name} --from-template {ns.tpl_name} "
                    f"--client-id-ref store:{nm}-client-id"
                    + (f" --client-secret-ref store:{nm}-client-secret" if ns.prereg_secret else "")])
        if ns.prereg_secret and not client_secret:
            raise GatewayError(
                "client_secret_needed", "this app is a confidential client and its secret was not given",
                hints=[f"lotr secret put {nm}-client-secret, then add "
                       f"--client-secret-ref store:{nm}-client-secret"])
    net = oauth.NetPolicy(ns.allow_private_network, ns.allow_insecure_localhost, meta_hosts)
    res = oauth.login(ns.url, net, scope=" ".join(ns.scope or []) or None, client_id=client_id,
                      client_secret=client_secret, cimd_url=ns.client_metadata_url,
                      authorization_server=ns.authorization_server,
                      offline_access=ns.offline_access, redirect_port=ns.redirect_port or 0,
                      send_resource=not ns.no_resource, redirect_host=ns.redirect_host,
                      force_browser=ns.open_browser, auth_params=getattr(ns, "auth_params", None),
                      say=_say, skip_audience_check=ns.skip_audience_check, confirm=_confirm(ns))
    entry, _drop = _finish_login(ns, reg, home, cid, res)
    if getattr(ns, "tpl_policy", None):
        entry["policy"].update(ns.tpl_policy)
    ref = entry["auth"]["token_ref"]
    if tpl_hosts is not None:
        extra = set(entry["network"]["hosts"]) - set(tpl_hosts)
        if extra:
            for r in (ref, entry["auth"]["oauth"].get("client_secret_ref")):
                if r:
                    _drop_secret(r)                # no orphan secret for a refused connection
            raise GatewayError("template_host_mismatch",
                               "discovery introduced host(s) the template does not vouch for: "
                               f"{', '.join(sorted(extra))}",
                               hints=[f"the template pins: {', '.join(tpl_hosts)}",
                                      "connect without --from-template if this is expected"])
    try:
        reg.add_connection(entry)
        reg.save(home / "registry.json")
    except BaseException:
        for r in (ref, entry["auth"]["oauth"].get("client_secret_ref")):
            if r:
                _drop_secret(r)                    # no orphan secret for a connection never saved
        raise
    notes = list(res["notes"])
    out = {"ok": True, "added": cid, "tools": len(entry["tools"]), "spec": oauth.SPEC_REVISION,
           "issuer": entry["auth"]["oauth"]["issuer"], "scopes": entry["auth"]["oauth"]["scopes"],
           "client_id_source": entry["auth"]["oauth"]["client_id_source"],
           "refresh_token": _level(ref), "pinned_hosts": entry["network"]["hosts"], "notes": notes}
    if meta_hosts:                       # 0.5.0: recorded on the connection and named in `lotr status`
        out["insecure_metadata_hosts"] = meta_hosts
        ep_host = (urlparse(entry["endpoint"]).hostname or "").lower()
        stray = [h for h in meta_hosts if h != ep_host]
        if stray:
            notes.append("--allow-insecure-metadata %s has no effect: protected-resource metadata is "
                         "read only from the endpoint's own host (%s)" % (", ".join(stray), ep_host))
    return out


def _oauth_conn(reg, cid):
    conn = reg.connections.get(cid)
    if conn is None:
        raise GatewayError("unknown_connection", f"no connection {cid!r}")
    if (conn.get("auth") or {}).get("scheme") != "oauth":
        raise GatewayError("not_oauth", f"{cid} is not an OAuth connection")
    return conn


def cmd_login(ns):
    from lotrlib import oauth
    home = _admin_home(ns)
    _, reg = _registry(home)
    cid = _conn_id(ns, reg)
    conn = _oauth_conn(reg, cid)
    _own_terminal_gate(f"login {cid}")
    block = conn["auth"]["oauth"]
    net = oauth.NetPolicy.from_block(block)
    pre_id = pre_secret = cimd = None
    if block["client_id_source"] == "preregistered":
        pre_id = block["client_id"]
        if block.get("client_secret_ref"):
            pre_secret = oauth.unwrap_secret(block, block["client_secret_ref"],
                                             _read_ref(block["client_secret_ref"]))
    elif block["client_id_source"] == "cimd":
        cimd = block["client_id"]
    old_rt = old_secret = None
    if conn["auth"].get("token_ref"):
        try:                                       # kept to revoke it once the new login is saved
            old_rt = oauth.unwrap_secret(block, conn["auth"]["token_ref"],
                                         _read_ref(conn["auth"]["token_ref"]))
            if block.get("client_secret_ref"):
                old_secret = oauth.unwrap_secret(block, block["client_secret_ref"],
                                                 _read_ref(block["client_secret_ref"]))
        except GatewayError:
            old_rt = None
    old_block = dict(block)
    res = oauth.login(conn["endpoint"], net, scope=" ".join(block.get("scopes") or []) or None,
                      extra_scope=" ".join(ns.scope or []).split(), client_id=pre_id,
                      client_secret=pre_secret, cimd_url=cimd,
                      authorization_server=block["issuer"], offline_access=False,
                      redirect_port=ns.redirect_port or 0, say=_say,
                      force_browser=ns.open_browser,
                      send_resource=block.get("send_resource", True),
                      redirect_host=block.get("redirect_host", "127.0.0.1"),
                      skip_audience_check=not block.get("audience_check", True),
                      auth_params=block.get("auth_params"), confirm=_confirm(ns))
    ns.l1 = False
    ns.url = conn["endpoint"]
    ns.identity = conn.get("identity")
    ns.trust = conn.get("trust")
    res["block"]["max_access_cache_s"] = block.get("max_access_cache_s", 3600)
    entry, drop = _finish_login(ns, reg, home, cid, res, existing=dict(conn))
    ref = entry["auth"]["token_ref"]
    reg.connections[cid] = entry
    reg.save(home / "registry.json")
    for r in drop:                                  # a ref the new login no longer uses
        _drop_secret(r)
    revoked = None
    if old_rt and old_rt != "":
        revoked = oauth.revoke(old_block, old_rt, "refresh_token", old_secret)
        old_rt = old_secret = None
    notes = list(res["notes"])
    if revoked is False:
        notes.append("the previous refresh token could not be revoked at the server")
    return {"ok": True, "logged_in": cid, "tools": len(entry["tools"]),
            "scopes": entry["auth"]["oauth"]["scopes"], "refresh_token": _level(ref),
            "old_token_revoked": revoked, "notes": notes}


def cmd_disconnect(ns):
    from lotrlib import oauth
    home = _admin_home(ns)
    _, reg = _registry(home)
    cid = _conn_id(ns, reg)
    conn = _oauth_conn(reg, cid)
    _own_terminal_gate(f"disconnect {cid}")
    block = conn["auth"]["oauth"]
    ref = conn["auth"].get("token_ref")
    revoked, notes = None, []
    if ref:
        try:
            secret = oauth.unwrap_secret(block, block["client_secret_ref"],
                                         _read_ref(block["client_secret_ref"])) \
                if block.get("client_secret_ref") else None
            rt = oauth.unwrap_secret(block, ref, _read_ref(ref))
            revoked = oauth.revoke(block, rt, "refresh_token", secret)
            rt = secret = None
        except GatewayError as e:
            revoked = False
            notes.append(f"could not read the refresh token to revoke it ({e.code}); the "
                         "connection is removed locally only")
    if revoked is None and ref is None:
        notes.append("no refresh token was stored, so nothing was revoked at the server; the "
                     "access token expires on its own")
    elif revoked is None:
        notes.append("the authorization server has no revocation endpoint (RFC 7009); the "
                     "token was deleted here but not invalidated there")
    elif revoked is False and not notes:
        notes.append("the authorization server did not confirm the revocation")
    removed = []
    for r in (ref, block.get("client_secret_ref")):
        if r:
            ok = _drop_secret(r)
            removed.append({"ref": r, "deleted": ok})
            if not ok:
                notes.append(f"{r} could not be deleted; remove it by hand")
    del reg.connections[cid]
    reg.save(home / "registry.json")
    oauth.forget(cid)
    return {"ok": True, "disconnected": cid, "revoked": revoked, "secrets": removed,
            "notes": notes}


def _hub_admin_gate(what, why="hub client enrolment"):
    """gt:hub:enroll with step-up, asked by THIS process (the authority identifies it from the
    kernel). Off = nothing happens (0.2.0). Raises GatewayError on any refusal."""
    from lotrlib import unlock
    if not unlock.enabled():
        return None
    return unlock.check("gt:hub:enroll", None, request=True,
                        reason=f"lotr {what} ({why})", answer=unlock.tty_answer())


def _looks_claude(ipc, info):
    """Is this process a Claude Code `claude`? The same marks gt core's authority uses."""
    try:
        path = ipc.process_path(info["pid"]).replace("\\", "/")
        args = ipc.process_args(info["pid"])
    except Exception:                                 # noqa: BLE001
        return False
    base = os.path.basename(path).lower()
    a0 = os.path.basename(args[0]).lower() if args else ""
    names = ("claude", "claude.exe")
    if base in names or a0 in names or (info.get("comm") or "").lower() in names:
        return True
    if "/claude/versions/" in path.lower():
        return True
    return any("@anthropic-ai/claude-code" in a for a in (args or [])[:3])


def _stdin_is_tty():
    return has_terminal(sys.stdin)


def _own_terminal_gate(what):
    """A sign-in is a person's act: refuse to run `connect` / `login` / `disconnect` from inside
    Claude Code's process tree (a model must not be able to start a login it could then ride),
    and, while gt unlock is on, require the step-up the registry already uses for hub
    administration. Honest limit: a same-user process can detach from the tree, so this stops a
    model's Bash tool, not a determined local program. Without gt core installed the process
    ancestry cannot be read: then Claude Code's CLAUDECODE marker (a convenience: `env -u
    CLAUDECODE` removes it) and whether stdin is a terminal are all that can be checked, and a
    sign-in with no terminal on stdin is refused."""
    from lotrlib import unlock
    hint = ["open your own terminal (not Claude Code's) and run the same command there"]
    if os.environ.get("CLAUDECODE"):
        raise GatewayError("oauth_not_a_terminal", f"lotr {what} was started from inside Claude "
                           "Code; a sign-in must be started by you, in your own terminal", hint)
    ipc = unlock.ipc()
    if ipc is None and not _stdin_is_tty():
        raise GatewayError("oauth_not_a_terminal", f"lotr {what} needs a terminal on stdin (gt "
                           "core is not installed here, so the process tree cannot be inspected "
                           "and the environment marker alone is not trusted)", hint)
    if ipc is not None:
        # FAIL CLOSED: if the process table cannot be read (any platform, Windows included), the
        # sign-in is refused rather than guessed at.
        try:
            chain = ipc.ancestry(os.getpid())
            tree_has_claude = any(_looks_claude(ipc, i) for i in chain[1:])
            person = ipc.interactive_chain(chain)
        except Exception:                             # noqa: BLE001
            raise GatewayError("oauth_not_a_terminal", f"lotr {what}: the process tree could not "
                               "be inspected, so it is refused", hint)
        if tree_has_claude:
            raise GatewayError("oauth_not_a_terminal", f"lotr {what} was started from inside "
                               "Claude Code's process tree; run it in your own terminal", hint)
        if not person:
            raise GatewayError("oauth_not_a_terminal", f"lotr {what} needs a person's "
                               "terminal (no interactive shell above this process)", hint)
    _hub_admin_gate(what, "OAuth sign-in administration")


def cmd_enroll(ns):
    home = _admin_home(ns)
    out = Path(ns.secret_out).expanduser()
    if os.path.lexists(out):
        raise GatewayError("secret_out_exists", f"{out} already exists; refusing to overwrite")
    _, reg = _registry(home)
    _hub_admin_gate(f"enroll {ns.client}")
    secret = reg.enroll(ns.client, ns.machine, ns.allow or ["*"], ns.max_tier)
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name != "nt":                   # no mode bits on Windows: the profile ACL
            os.fchmod(fd, 0o600)
        os.write(fd, (secret + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    del secret
    try:
        reg.save(home / "registry.json")
    except BaseException:
        os.unlink(out)                        # no orphan secret for a client never saved
        raise
    return {"ok": True, "client": ns.client, "secret_out": str(out),
            "next": f"copy {out} to the client machine and set hub.credential_ref to file:<path>"}


def cmd_revoke(ns):
    home = _admin_home(ns)
    _, reg = _registry(home)
    _hub_admin_gate(f"revoke {ns.client}")
    reg.revoke(ns.client)
    reg.save(home / "registry.json")
    return {"ok": True, "revoked": ns.client}


def cmd_connections(ns):
    home = _admin_home(ns)
    _, reg = _registry(home)
    conns = [{k: c.get(k) for k in ("id", "identity", "profile", "base_url", "trust", "enabled")}
             for c in reg.connections.values()]
    clients = [{k: c.get(k) for k in ("id", "machine", "allow", "max_tier", "revoked")}
               for c in reg.clients.values()]
    return {"ok": True, "zone": reg.zone, "connections": conns, "clients": clients}


# ------------------------------------------------------------------ parser

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"lotr: {message}\n")
        sys.exit(2)


def build_parser(setup_template_optional=False):
    p = _Parser(prog="lotr", description="gt-lotr command line")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--home", help="gateway home (default ~/.config/gt-lotr/<zone>)")
    g.add_argument("--zone", help="zone name for the default home (default personal)")
    sub = p.add_subparsers(dest="cmd", parser_class=_Parser)
    sub.required = True

    f = sub.add_parser("find")
    f.add_argument("query", nargs="?", default="")
    f.add_argument("--connection")
    f.add_argument("--limit", type=int)
    f.add_argument("--schema", action="store_true")
    f.add_argument("--role", choices=("reader", "writer", "combined"),
                   help="the agent you are: only the tiers its tools carry are listed")
    f.set_defaults(fn=cmd_find)

    d = sub.add_parser("describe", help="one op: what it does, its inputs, its risk, and a "
                                        "single-use reference to run it by")
    d.add_argument("conn")
    d.add_argument("op")
    d.add_argument("--role", choices=("reader", "writer", "combined"))
    d.set_defaults(fn=cmd_describe)

    for name in ("read", "write", "consent"):
        c = sub.add_parser(name)
        c.add_argument("conn")
        c.add_argument("op")
        c.add_argument("kv", nargs="*", help="k=v args (v parsed as JSON when it parses)")
        c.add_argument("--args", help="JSON object of args")
        c.add_argument("--select")
        c.add_argument("--cursor")
        c.set_defaults(fn=cmd_call)

    st = sub.add_parser("status", help="every connection and whether it works (like /mcp)")
    st.add_argument("--check", action="store_true",
                    help="test every enabled MCP connection now (one read-only tools/list each)")
    st.add_argument("--table", action="store_true",
                    help="a readable table instead of JSON (the view /gt:gt-settings lotr shows)")
    st.set_defaults(fn=cmd_status)
    sub.add_parser("catalog").set_defaults(fn=cmd_catalog)

    i = sub.add_parser("init", help="create a gateway home (a hub can serve MCP over HTTP: listen.mcp_http)")
    i.add_argument("--zone", dest="init_zone")
    i.add_argument("--mode", choices=MODES, default="local")
    i.set_defaults(fn=cmd_init)

    a = sub.add_parser("add-http")
    a.add_argument("id")
    a.add_argument("--profile")
    a.add_argument("--base-url")
    a.add_argument("--identity")
    a.add_argument("--auth", choices=("bearer", "basic", "header", "none"))
    a.add_argument("--token-ref")
    a.add_argument("--user")
    a.add_argument("--header")
    a.add_argument("--description")
    a.add_argument("--trust", choices=("T0", "T1", "T2"))
    a.add_argument("--from-template", dest="from_template", metavar="NAME",
                   help="pre-fill from a kind-http template (lotr template list)")
    a.set_defaults(fn=cmd_add_http)

    sd = sub.add_parser("add-stdio", help="an MCP server LOTR launches and talks to over stdio")
    sd.add_argument("id")
    sd.add_argument("--command", dest="command_line", metavar="CMDLINE", help="the command line, split into an argv (no shell is used)")
    sd.add_argument("--env", action="append", metavar="NAME=REF",
                    help="an environment variable for the process, as a secret REF (repeatable)")
    sd.add_argument("--env-plain", action="append", metavar="NAME=VALUE", dest="env_plain",
                    help="a plain, non-secret environment variable (a value that looks like a "
                         "secret is refused; secrets are --env refs)")
    sd.add_argument("--param", action="append", metavar="NAME=VALUE",
                    help="a value for a {name} the template's command asks for (a folder, a repo)")
    sd.add_argument("--runtime", choices=("node", "uv", "docker", "binary", "python"),
                    help="what the command runs on; checked before launch")
    sd.add_argument("--cwd")
    sd.add_argument("--identity")
    sd.add_argument("--description")
    sd.add_argument("--trust", choices=("T0", "T1", "T2"))
    sd.add_argument("--from-template", dest="from_template", metavar="NAME")
    sd.set_defaults(fn=cmd_add_stdio)

    t = sub.add_parser("template", help="write your own connection template: guide, new, check, list")
    tsub = t.add_subparsers(dest="tcmd", parser_class=_Parser)
    tsub.required = True
    tsub.add_parser("guide", help="how a template works, field by field, with examples")
    tn = tsub.add_parser("new", help="scaffold a template file with TODOs")
    tn.add_argument("name")
    tn.add_argument("--kind", choices=("http", "mcp-stdio", "mcp-oauth", "mcp-http"), default="http")
    tn.add_argument("--archetype", choices=ARCHETYPE_NAMES,
                    help="start from a shipped archetype (see `lotr template guide`)")
    tn.add_argument("--from-doc", dest="from_doc", metavar="FILE",
                    help="a text file of the API's documentation (or - for stdin) to read hints from")
    tn.add_argument("--out", help="write here instead of the connections/ folder of this home")
    tn.add_argument("--force", action="store_true")
    tc = tsub.add_parser("check", help="validate a template file or name, in plain words")
    tc.add_argument("file")
    tf = tsub.add_parser("facts-check", help="check a research run's fact sheet (sources, states)")
    tf.add_argument("file")
    tf.add_argument("--template", help="the template file or name to compare it with")
    tu = tsub.add_parser("update", help="fetch the template catalog (creates no connection)")
    tu.add_argument("--ref", default="main", help="branch, tag or commit of the public repository")
    tu.add_argument("--url", help="a catalog folder URL of your own instead of the public repository")
    tu.add_argument("--only", action="append", metavar="NAME", help="just this template (repeatable)")
    tu.add_argument("--dry-run", action="store_true", help="show what would be installed")
    tu.add_argument("--force", action="store_true", help="accept an older catalog than the installed one")
    tu.add_argument("--allow-insecure-localhost", action="store_true",
                    help="allow plain http to a loopback --url (tests)")
    tsub.add_parser("list", help="shipped, catalog and your own templates")
    t.set_defaults(fn=cmd_template)

    su = sub.add_parser("setup", help="set a service up from its template: check, credential, "
                                      "add, test")
    if setup_template_optional:
        su.add_argument("template", nargs="?", default=None,
                        help="a template name (lotr template list); none: suggestions for your persona")
    else:
        su.add_argument("template", help="a template name (lotr template list)")
    su.add_argument("--id", help="connection id name@zone (default: the template name in this zone)")
    su.add_argument("--identity", help="who you are there (default: the template's, else generic)")
    su.add_argument("--token-ref", help="use this credential reference instead of the template's")
    su.add_argument("--client-id-ref", help="reference to the OAuth app's client id")
    su.add_argument("--client-secret-ref", help="reference to the OAuth app's client secret")
    su.add_argument("--param", action="append", metavar="NAME=VALUE",
                    help="a value the template asks for, e.g. a folder (repeatable)")
    su.add_argument("--yes", action="store_true",
                    help="agree to run the template's program / skip the sign-in confirmation")
    su.add_argument("--no-prompt", action="store_true", help="never ask for a credential here")
    su.add_argument("--no-smoke", action="store_true", help="skip the final test")
    su.add_argument("--json", action="store_true", help="JSON instead of plain words")
    su.set_defaults(fn=cmd_setup)

    sc = sub.add_parser("secret", help="put a credential in the store from your own terminal")
    scs = sc.add_subparsers(dest="scmd", parser_class=_Parser)
    scs.required = True
    sp = scs.add_parser("put", help="type a value at a hidden prompt; stored as store:NAME")
    sp.add_argument("name")
    sp.add_argument("--force", action="store_true", help="replace a value that is already there")
    sk = scs.add_parser("check", help="is a reference there? (never prints a value)")
    sk.add_argument("ref")
    sc.set_defaults(fn=cmd_secret)

    pl = sub.add_parser("pull", help="the MCP servers Claude Code already has configured, and the "
                                     "command that brings each into LOTR (read-only unless --import)")
    pl.add_argument("--import", dest="import_names", action="append", default=[], metavar="NAME",
                    help="register this server by reference to the token file a client already keeps "
                         "for it (repeatable; asks first; there is no 'all')")
    pl.add_argument("--dry-run", action="store_true",
                    help="with --import: show what would be registered and change nothing "
                         "(the listing alone is always read-only)")
    pl.add_argument("--yes", action="store_true", help="with --import: do not wait for the confirmation "
                                                       "(the summary still prints)")
    pl.add_argument("--from", dest="from_file", action="append", default=[], metavar="FILE",
                    help="also read the mcpServers of this file, e.g. a project's .mcp.json (repeatable)")
    pl.add_argument("--no-probe", action="store_true",
                    help="do not contact any server to look for OAuth metadata")
    pl.add_argument("--allow-private-network", action="store_true",
                    help="let the OAuth-metadata probe reach private addresses, and carry the flag "
                         "into the connect commands it prints")
    pl.add_argument("--table", action="store_true", help="a readable view instead of JSON")
    pl.set_defaults(fn=cmd_pull)

    m = sub.add_parser("add-mcp")
    m.add_argument("id")
    m.add_argument("--endpoint")
    m.add_argument("--transport", default="http", choices=("http",))
    m.add_argument("--identity")
    m.add_argument("--from-template", dest="from_template", metavar="NAME",
                   help="pre-fill from a kind-mcp-http template (a hosted MCP server with a key)")
    m.add_argument("--auth-header", dest="auth_header",
                   help="send the key in this header instead of Authorization: Bearer")
    m.add_argument("--auth-prefix", dest="auth_prefix", metavar="PREFIX",
                   help='send the key as Authorization: PREFIXkey, e.g. "Token token=" (0.5.0)')
    m.add_argument("--auth-ref", help="a secret REF to the bearer token, e.g. "
                                      "file:/abs/tokens.json#access_token -- never the token")
    m.add_argument("--refresh-cmd", help="the MCP client's own refresh helper, run on HTTP 401")
    m.add_argument("--description")
    m.add_argument("--trust", choices=("T0", "T1", "T2"))
    m.set_defaults(fn=cmd_add_mcp)

    c = sub.add_parser("connect")
    c.add_argument("name", help="connection id (name@zone), or a bare name in this zone")
    c.add_argument("--from-template", dest="from_template", metavar="NAME",
                   help="pre-fill the connect inputs from a shipped/per-home connection template "
                        "(templates/connections/<NAME>.json); explicit flags still win")
    c.add_argument("--url", help="the https MCP endpoint (required unless --from-template supplies "
                                 "one)")
    c.add_argument("--scope", action="append",
                   help="a scope; repeat the flag for several (default: what the server asks for)")
    c.add_argument("--client-id", help="a pre-registered OAuth client id")
    c.add_argument("--client-id-ref", help="a reference to the pre-registered client id (0.5.0)")
    c.add_argument("--client-secret-ref", help="a reference to that client's secret (0.5.0)")
    c.add_argument("--client-secret-file", help="file holding that client's secret (never argv)")
    c.add_argument("--client-secret-stdin", action="store_true")
    c.add_argument("--client-secret-prompt", action="store_true",
                   help="type the client's secret at a hidden prompt")
    c.add_argument("--open-browser", action="store_true",
                   help="open the browser even without a terminal on stdin (never under "
                        "GT_LOTR_NO_BROWSER=1)")
    c.add_argument("--no-resource", action="store_true",
                   help="do not send the RFC 8707 `resource` parameter (for a provider that "
                        "rejects it)")
    c.add_argument("--redirect-host", default="127.0.0.1", choices=("127.0.0.1", "localhost"),
                   help="host NAME in the redirect URI (the listener binds 127.0.0.1 either way)")
    c.add_argument("--client-metadata-url", help="https URL of a Client ID Metadata Document "
                                                 "you host (used when the server supports them)")
    c.add_argument("--authorization-server", help="issuer to use when the server lists several")
    c.add_argument("--redirect-port", type=int, help="fixed loopback port (default: ephemeral)")
    c.add_argument("--offline-access", action="store_true",
                   help="also ask for a refresh token via the offline_access scope")
    c.add_argument("--l1", action="store_true",
                   help="store the refresh token as a mode-600 file even while gt unlock is on")
    c.add_argument("--allow-insecure-localhost", action="store_true",
                   help="allow plain http to a loopback server you name (tests, local servers)")
    c.add_argument("--allow-private-network", action="store_true",
                   help="allow servers on private/loopback addresses (SSRF guard off for THIS "
                        "connection)")
    c.add_argument("--allow-insecure-metadata", action="append", metavar="HOST",
                   help="vouch for HOST (repeatable): follow its protected-resource metadata over "
                        "plain http when the server advertises it that way. Only that one document; "
                        "the token and authorize endpoints must still be https. Kept on the "
                        "connection and shown in `lotr status`")
    c.add_argument("--skip-audience-check", action="store_true",
                   help="do not compare a JWT access token's aud with the resource")
    c.add_argument("--yes", action="store_true",
                   help="do not wait for the confirmation (the summary still prints)")
    c.add_argument("--identity")
    c.add_argument("--description")
    c.add_argument("--trust", default="T1", choices=("T0", "T1", "T2"))
    c.set_defaults(fn=cmd_connect)

    lg = sub.add_parser("login")
    lg.add_argument("name")
    lg.add_argument("--scope", action="append",
                    help="an extra scope to add to those already granted; repeat for several")
    lg.add_argument("--redirect-port", type=int)
    lg.add_argument("--yes", action="store_true")
    lg.add_argument("--open-browser", action="store_true")
    lg.set_defaults(fn=cmd_login)

    dc = sub.add_parser("disconnect")
    dc.add_argument("name")
    dc.set_defaults(fn=cmd_disconnect)

    e = sub.add_parser("enroll")
    e.add_argument("client")
    e.add_argument("--machine", required=True)
    e.add_argument("--allow", nargs="+")
    e.add_argument("--max-tier", default="read", choices=("read", "write", "consent"))
    e.add_argument("--secret-out", required=True)
    e.set_defaults(fn=cmd_enroll)

    r = sub.add_parser("revoke")
    r.add_argument("client")
    r.set_defaults(fn=cmd_revoke)

    sub.add_parser("connections").set_defaults(fn=cmd_connections)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser(setup_template_optional=_setup_template_optional(argv))
    ns, extra = parser.parse_known_args(argv)
    if extra:
        # k=v pairs given after an option (`read C OP --select S k=v`) land here
        if ns.cmd in ("read", "write", "consent") and all(
                "=" in x and not x.startswith("-") for x in extra):
            ns.kv = list(ns.kv) + extra
        else:
            parser.error("unrecognized arguments: " + " ".join(extra))
    try:
        result = ns.fn(ns)
    except _Usage as e:
        sys.stderr.write(f"lotr: {e}\n")
        return 2
    except GatewayError as e:
        _out({"ok": False, "error": e.to_dict()})
        return 1
    except Exception as e:                    # noqa: BLE001 - JSON out, never a traceback
        _out({"ok": False, "error": {"code": "internal", "message": type(e).__name__, "hints": []}})
        return 1
    if isinstance(result, _Text):
        try:                      # a cp1252 console cannot show ✓/✗: replace, never crash
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        sys.stdout.write(result)
        return getattr(result, "rc", 0)
    _out(result)
    if isinstance(result, dict) and result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

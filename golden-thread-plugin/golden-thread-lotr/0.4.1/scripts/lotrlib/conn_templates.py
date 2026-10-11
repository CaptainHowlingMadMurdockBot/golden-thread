"""Shipped and per-home connection templates.

A connection template is a small, secret-free descriptor that pre-fills the inputs a human
would otherwise type on `lotr connect`: the MCP endpoint, the scopes, the audience/resource
posture, the trust tier, and the host(s) the connection is pinned to. It is NOT a registry
entry and NOT an `auth.oauth` block -- the issuer, the OAuth endpoints, the client_id and every
token/ref are discovered or minted live, per user, at connect time (see oauth.login). A template
therefore carries ZERO secret material, and that is enforced here on load, not merely documented.

Templates are read LIVE from two dirs, the exact idiom lotr already uses for recipes
(engine.py SHIPPED_RECIPES + home/recipes, recipes.load_recipes): the plugin's shipped
`templates/connections/` and the per-home `~/.config/gt-lotr/<zone>/connections/`, with a
later dir's template replacing an earlier one's by name. Nothing is ever copied into a vault
or anywhere else; a template is only consulted when creating a brand-new connection.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .errors import GatewayError

# where the plugin's shipped templates live, relative to this file (mirrors engine.SHIPPED_RECIPES):
#   lotrlib/conn_templates.py  ->  parents[2] is the version dir  ->  templates/connections
SHIPPED_CONNECTIONS = Path(__file__).resolve().parents[2] / "templates" / "connections"

_NAME = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SCOPE = re.compile(r"^\S+$")          # a scope token carries no spaces (mirrors registry scopes)
_KIND = ("mcp-oauth",)                  # the only connection kind a template describes in this release

# keys that could smuggle a credential into a shipped file -- refused outright
_SECRET_KEYS = ("token_ref", "client_secret", "client_secret_ref", "client_secret_file",
                "client_id", "access_token", "refresh_token", "secret", "password", "token",
                "auth", "oauth")

_TOP_KEYS = {"schema", "name", "summary", "kind", "connect", "network", "notes", "source"}
_CONNECT_KEYS = {"url", "scopes", "offline_access", "skip_audience_check", "send_resource",
                 "redirect_host", "trust", "identity", "description"}
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_TRUST = ("T0", "T1", "T2")
_REDIRECT_HOSTS = ("127.0.0.1", "localhost")


def _invalid(src, msg):
    raise GatewayError("template_invalid", f"{src}: {msg}")


def _has_secret_key(obj):
    """True if any key anywhere in the structure is a credential-shaped key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SECRET_KEYS:
                return k
            found = _has_secret_key(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _has_secret_key(v)
            if found:
                return found
    return None


def validate(tpl, src="<template>"):
    if not isinstance(tpl, dict):
        _invalid(src, "a connection template must be a JSON object")
    # the zero-secret guarantee, enforced -- refuse a file that could carry a credential
    bad = _has_secret_key(tpl)
    if bad:
        raise GatewayError("template_has_secret",
                           f"{src}: a connection template may not contain {bad!r}; the client_id "
                           "and every token/ref are minted per user at connect time")
    extra = set(tpl) - _TOP_KEYS
    if extra:
        _invalid(src, f"unknown key(s): {', '.join(sorted(extra))}")
    if tpl.get("schema") != 1:
        _invalid(src, "'schema' must be 1")
    name = tpl.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        _invalid(src, "'name' must be a lowercase id like 'jira'")
    if not isinstance(tpl.get("summary"), str) or not tpl["summary"].strip():
        _invalid(src, "'summary' must be a non-empty string")
    if tpl.get("kind") not in _KIND:
        _invalid(src, f"'kind' must be one of {', '.join(_KIND)}")
    conn = tpl.get("connect")
    if not isinstance(conn, dict) or not conn:
        _invalid(src, "'connect' must be a non-empty object")
    extra = set(conn) - _CONNECT_KEYS
    if extra:
        _invalid(src, f"connect.* unknown key(s): {', '.join(sorted(extra))}")
    url = conn.get("url")
    if not isinstance(url, str):
        _invalid(src, "connect.url must be a string")
    u = urlparse(url)
    # https only; plain http is admitted for a loopback host alone (a local MCP server, and the
    # test world), and `lotr connect` still needs --allow-insecure-localhost to use it
    if not u.hostname or not (u.scheme == "https"
                              or (u.scheme == "http" and u.hostname in _LOOPBACK)):
        _invalid(src, "connect.url must be an https URL with a host (http only for loopback)")
    scopes = conn.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(s, str) and _SCOPE.match(s) for s in scopes):
        _invalid(src, "connect.scopes must be a list of scope tokens (no spaces)")
    for b in ("offline_access", "skip_audience_check", "send_resource"):
        if b in conn and not isinstance(conn[b], bool):
            _invalid(src, f"connect.{b} must be true or false")
    if "redirect_host" in conn and conn["redirect_host"] not in _REDIRECT_HOSTS:
        _invalid(src, f"connect.redirect_host must be one of {', '.join(_REDIRECT_HOSTS)}")
    if "trust" in conn and conn["trust"] not in _TRUST:
        _invalid(src, f"connect.trust must be one of {', '.join(_TRUST)}")
    for s in ("identity", "description"):
        if s in conn and not isinstance(conn[s], str):
            _invalid(src, f"connect.{s} must be a string")
    net = tpl.get("network", {})
    if not isinstance(net, dict):
        _invalid(src, "'network' must be an object")
    hosts = net.get("hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
        _invalid(src, "network.hosts must be a list of non-empty host strings")
    # the url's host must be one the template vouches for
    if hosts and u.hostname not in hosts:
        _invalid(src, f"connect.url host {u.hostname!r} is not in network.hosts")
    notes = tpl.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        _invalid(src, "'notes' must be a list of strings")
    return tpl


def load_templates(dirs):
    """Every *.json template under `dirs`; a later dir's template replaces an earlier one by name."""
    by_name = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise GatewayError("template_invalid", f"{f}: not valid JSON: {e}")
            validate(data, str(f))
            data = dict(data, source=str(f))
            by_name[data["name"]] = data
    return by_name


def get_template(home, name):
    """Resolve one template by name across shipped + per-home dirs, or raise with hints."""
    import difflib
    templates = load_templates([SHIPPED_CONNECTIONS, Path(home) / "connections"])
    tpl = templates.get(name)
    if tpl is None:
        hints = difflib.get_close_matches(name, list(templates), n=3)
        raise GatewayError("unknown_template", f"no connection template {name!r}",
                           hints=([f"did you mean: {', '.join(hints)}"] if hints else
                                  [f"available: {', '.join(sorted(templates)) or '(none)'}"]))
    return tpl

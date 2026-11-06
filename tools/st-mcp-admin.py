#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ST-MCP 租户管理界面（零依赖，只用 Python 标准库）。

启动：sh tools/start-admin.sh          然后浏览器打开 http://127.0.0.1:8001
      常驻：sh tools/install-admin-service.sh（systemd，监听 0.0.0.0:8001）

单端口多租户：所有租户共用同一个 MCP 地址，地址里的 token 决定用哪个库账号。
页面上的"开通/删除"调 tools/ 下的脚本，数据来自交付包里的租户注册表
<包目录>/tenants/tenants.json（600 权限，含库口令与 token）。

访问控制：本机请求免登录；任何非本机请求必须通过 HTTP Basic 认证，账号口令来自
secrets/admin.env（600）。可选 ADMIN_ALLOW_CIDR=10.0.0.0/24 再做来源网段限制。
没有登录配置时，--allow-remote 会拒绝启动（fail closed）。
"""

import argparse
import base64
import hmac
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "tools")
DATA = os.environ.get("ST_ADMIN_DATA") or os.path.join(HERE, "tenants")
CREATE = os.path.join(TOOLS, "create-tenant.sh")
LIST = os.path.join(TOOLS, "list-tenants.sh")
DELETE = os.path.join(TOOLS, "delete-tenant.sh")
AUTH_FILE = os.environ.get("ST_ADMIN_AUTH_FILE") or os.path.join(HERE, "secrets", "admin.env")


def find_shell():
    """Linux 用 /bin/sh；Windows 上用 Git Bash（/bin/sh 对原生 Python 不可见）。"""
    if os.path.exists("/bin/sh"):
        return "/bin/sh"
    for cand in ("bash", "sh"):
        p = shutil.which(cand)
        if p:
            return p
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(p):
            return p
    return None


SHELL = find_shell()


def native(path):
    return path.replace("\\", "/") if os.sep == "\\" else path


def run_script(script, args, timeout=900, stdin_text=None):
    if not SHELL:
        return 1, "", "找不到 sh/bash：请从 Git Bash 或 Linux shell 里启动本程序"
    if not os.path.exists(script):
        return 1, "", "缺少脚本：%s" % script
    cmd = [SHELL, native(script)] + args
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            input=None if stdin_text is None else stdin_text.encode("utf-8"),
        )
        out = p.stdout.decode("utf-8", "replace").strip()
        err = p.stderr.decode("utf-8", "replace").strip()
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        return 1, "", "脚本执行超时（%ss）" % timeout


def last_json(text):
    """脚本可能先打日志再打 JSON，取最后一行能解析成 JSON 对象的。"""
    for line in reversed("" if not text else text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def pkg_dir():
    """与 tools/create-tenant.sh 同规则：ST_MCP_PKG > packages/ 下已解压的那一版 > 默认名。"""
    env = os.environ.get("ST_MCP_PKG")
    if env:
        return env
    base = os.path.join(HERE, "packages")
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            cand = os.path.join(base, name)
            if name.startswith("st-mcp-offline-") and os.path.exists(os.path.join(cand, "deploy.conf")):
                return cand
    return os.path.join(base, "st-mcp-offline-arm64-20261015")


def conf_get(path, key, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def registry_path():
    env = os.environ.get("ST_TENANT_REGISTRY_FILE")
    if env:
        return env
    return os.path.join(pkg_dir(), "tenants", "tenants.json")


def read_kv_file(path):
    conf = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                conf[key.strip()] = val.strip()
    except OSError:
        pass
    return conf


def auth_config(path):
    """登录配置来自 secrets/admin.env（600）；环境变量可覆盖，方便临时改。"""
    conf = read_kv_file(path)
    return {
        "user": os.environ.get("ST_ADMIN_USER") or conf.get("ADMIN_USER", ""),
        "password": os.environ.get("ST_ADMIN_PASSWORD") or conf.get("ADMIN_PASSWORD", ""),
        "allow_cidr": os.environ.get("ST_ADMIN_ALLOW_CIDR") or conf.get("ADMIN_ALLOW_CIDR", ""),
        "file": path,
    }


def parse_cidrs(text):
    nets = []
    for item in (text or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print("警告：忽略无法解析的网段 %r" % item, file=sys.stderr)
    return nets


def read_registry():
    path = registry_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        doc = json.load(fh)
    return doc.get("tenants") or []


def local_ip():
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def base_url(server_ip=""):
    pkg = pkg_dir()
    conf = os.path.join(pkg, "deploy.conf")
    port = conf_get(conf, "HOST_PORT", "8100")
    path = conf_get(conf, "MCP_PATH", "/mcp")
    return "http://%s:%s%s" % (server_ip or local_ip(), port, path)


def docker_info():
    docker = shutil.which("docker") is not None
    pkg = pkg_dir()
    container = conf_get(os.path.join(pkg, "deploy.conf"), "CONTAINER_NAME", "st-mcp")
    running = False
    tenant_mode = False
    if docker:
        try:
            names = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, timeout=10
            ).stdout.decode("utf-8", "replace").split()
            running = container in names
            if running:
                tenant_mode = subprocess.run(
                    ["docker", "exec", container, "printenv", "ST_TENANT_REGISTRY"],
                    capture_output=True,
                    timeout=10,
                ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return {"docker": docker, "container": container, "running": running, "tenant_mode": tenant_mode}


def mode_info():
    pkg = pkg_dir()
    info = docker_info()
    try:
        tenants = len(read_registry())
    except (OSError, ValueError):
        tenants = 0
    if not info["docker"]:
        mode = "preview"
    elif info["running"] and not info["tenant_mode"]:
        mode = "old-image"
    else:
        mode = "real"
    return {
        "docker": info["docker"],
        "container": info["container"],
        "container_running": info["running"],
        "tenant_mode": info["tenant_mode"],
        "package_path": pkg,
        "registry": registry_path(),
        "base_url": base_url(),
        "tenants": tenants,
        "deploy_mode": mode,
        "shell": SHELL or "",
    }


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ST-MCP 租户管理</title>
<style>
  :root { --bd:#d8dee9; --bg:#f6f8fa; --pri:#1f6feb; --danger:#c62828; --ok:#1a7f37; }
  * { box-sizing: border-box; }
  body { margin:0; padding:28px 20px 60px; background:var(--bg); color:#1f2328;
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  .wrap { max-width:1240px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#57606a; font-size:13px; margin-bottom:18px; }
  .box { background:#fff; border:1px solid var(--bd); border-radius:8px; padding:18px 20px; margin-bottom:18px; }
  h2 { font-size:15px; margin:0 0 14px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(185px,1fr)); gap:12px; }
  label { display:block; font-size:12px; color:#57606a; margin-bottom:4px; }
  input,select { width:100%; padding:8px 10px; border:1px solid var(--bd); border-radius:6px; font-size:14px; }
  input:focus,select:focus { outline:2px solid #cfe3ff; border-color:var(--pri); }
  button { padding:9px 16px; border:1px solid var(--bd); background:#fff; border-radius:6px;
           cursor:pointer; font-size:14px; }
  button:hover { background:#f0f3f6; }
  button.primary { background:var(--pri); border-color:var(--pri); color:#fff; }
  button.primary:hover { background:#1a5fd0; }
  button.danger { color:var(--danger); border-color:#f0c2c2; }
  button.small { padding:4px 10px; font-size:12px; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:9px 8px; border-bottom:1px solid #eaeef2; vertical-align:top; }
  th { background:#f6f8fa; font-weight:600; color:#57606a; white-space:nowrap; }
  code,pre { font-family:Consolas,Menlo,monospace; }
  code { background:#f0f3f6; padding:1px 5px; border-radius:4px; font-size:12px; word-break:break-all; }
  pre { background:#0d1117; color:#e6edf3; padding:12px 14px; border-radius:6px; overflow:auto;
        font-size:12px; margin:10px 0 0; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px; }
  .b-real { background:#dafbe1; color:var(--ok); }
  .b-preview { background:#fff8c5; color:#7d4e00; }
  .b-err { background:#ffebe9; color:var(--danger); }
  .muted { color:#57606a; font-size:12px; }
  .toast { position:fixed; right:24px; bottom:24px; background:#1f2328; color:#fff;
           padding:10px 16px; border-radius:6px; opacity:0; transition:opacity .25s; }
  .toast.show { opacity:1; }
</style>
</head>
<body>
<div class="wrap">
  <h1>ST-MCP 租户管理</h1>
  <div class="sub" id="sub">正在读取运行环境…</div>

  <div class="box">
    <h2>开通租户</h2>
    <form id="form">
      <div class="grid">
        <div><label>租户名 *</label><input id="tenant" placeholder="tenant_alice" required></div>
        <div><label>库用户名</label><input id="db_user" placeholder="留空 = TENANT_ALICE"></div>
        <div><label>数据库口令</label><input id="password" placeholder="留空自动生成"></div>
        <div><label>数据库地址</label><input id="db_host" placeholder="127.0.0.1"></div>
        <div><label>数据库端口</label><input id="db_port" placeholder="2003"></div>
        <div><label>数据库名</label><input id="db_name" placeholder="OSRDB"></div>
        <div><label>对外访问 IP</label><input id="server_ip" placeholder="用户连的地址，留空自动探测"></div>
        <div><label>访问模式</label><select id="access_mode">
          <option value="unrestricted">可读可写（unrestricted）</option>
          <option value="restricted">只读（restricted）</option>
        </select></div>
      </div>
      <div class="row">
        <button class="primary" type="submit" id="btnCreate">开通租户</button>
        <span class="muted">一条命令完成：建库账号 + 发 token。用户只拿到一个 MCP 地址，库口令不下发。</span>
      </div>
    </form>
    <pre id="result" style="display:none"></pre>
  </div>

  <div class="box">
    <h2>已有租户 <span class="badge" id="count">0</span></h2>
    <div class="row" style="margin:0 0 12px">
      <button class="small" onclick="refresh()">刷新</button>
      <span class="muted">注册表：<code id="recpath"></code>（600 权限，含库口令与 token，勿外拷）</span>
    </div>
    <table>
      <thead><tr>
        <th>租户</th><th>库账号</th><th>库口令</th><th>访问模式</th><th>客户端地址（含 token）</th><th>操作</th>
      </tr></thead>
      <tbody id="tbody"><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>
    </table>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);
let info = {};

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  try { return JSON.parse(text); } catch (e) { return {success:false,error:text}; }
}

async function loadInfo() {
  info = await api('/api/info');
  const mode = info.deploy_mode;
  let badge, hint;
  if (mode === 'preview') {
    badge = '<span class="badge b-preview">预览模式</span>';
    hint = ' · 本机没有 docker：只写注册表，库账号需手工建';
  } else if (mode === 'old-image') {
    badge = '<span class="badge b-err">旧镜像</span>';
    hint = ' · 容器 ' + esc(info.container) + ' 还认不出注册表：请在包目录重跑 sh install-all.sh';
  } else {
    badge = '<span class="badge b-real">单端口多租户</span>';
    hint = info.container_running
      ? ' · 容器 ' + esc(info.container) + ' 运行中，所有租户共用一个地址'
      : ' · 容器 ' + esc(info.container) + ' 未运行';
  }
  $('sub').innerHTML = badge + ' 服务地址 <code>' + esc(info.base_url) + '</code>' + hint;
  $('recpath').textContent = String(info.registry || '').replace(/\\/g, '/');
}

async function refresh() {
  const data = await api('/api/list');
  const rows = (data.tenants || []).slice().reverse();
  $('count').textContent = rows.length;
  if (!rows.length) {
    $('tbody').innerHTML = '<tr><td colspan="6" class="muted">还没有租户</td></tr>';
    return;
  }
  $('tbody').innerHTML = rows.map(t => {
    const url = (info.base_url || '') + '/' + t.token;
    const cfg = JSON.stringify({mcpServers: {['st-mcp-' + t.name]: {url: url}}});
    return '<tr>' +
      '<td><b>' + esc(t.name) + '</b><div class="muted">' + esc(t.created_at) + '</div></td>' +
      '<td><code>' + esc(t.user) + '</code><div class="muted">' + esc(t.host) + ':' + esc(t.port) + '/' + esc(t.database) + '</div></td>' +
      '<td><code>' + esc(t.password) + '</code></td>' +
      '<td>' + (t.access_mode === 'restricted'
        ? '<span class="badge b-preview">只读</span>'
        : '<span class="badge b-real">可写</span>') + '</td>' +
      '<td><code>' + esc(url) + '</code></td>' +
      '<td><button class="small" onclick="copyCfg(this)" data-cfg="' + esc(cfg) + '">复制配置</button> ' +
          '<button class="small danger" onclick="del(\'' + esc(t.name) + '\')">删除</button></td>' +
      '</tr>';
  }).join('');
}

function copyCfg(btn) {
  const text = btn.getAttribute('data-cfg');
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => toast('客户端配置已复制'));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('客户端配置已复制');
  }
}

$('form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const btn = $('btnCreate'); btn.disabled = true; btn.textContent = '开通中…';
  const body = {
    tenant: $('tenant').value.trim(),
    db_user: $('db_user').value.trim(),
    password: $('password').value,
    db_host: $('db_host').value.trim(),
    db_port: $('db_port').value.trim(),
    db_name: $('db_name').value.trim(),
    server_ip: $('server_ip').value.trim(),
    access_mode: $('access_mode').value
  };
  const res = await api('/api/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  btn.disabled = false; btn.textContent = '开通租户';
  const box = $('result'); box.style.display = 'block';
  box.textContent = JSON.stringify(res, null, 2);
  if (res.success) { $('form').reset(); toast('租户已开通'); refresh(); }
});

async function del(tenant) {
  if (!confirm('确定删除租户 "' + tenant + '"？\n删除后这个 token 立即失效，数据库账号保留。')) return;
  const res = await api('/api/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({tenant})});
  alert(res.success ? res.message + '\n' + (res.note || '') : ('删除失败：' + res.error));
  if (res.success) refresh();
}

loadInfo().then(refresh);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "st-mcp-admin/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _is_loopback(self):
        addr = self.client_address[0]
        if addr in ("127.0.0.1", "::1"):
            return True
        try:
            return ipaddress.ip_address(addr).is_loopback
        except ValueError:
            return False

    def _remote_ok(self):
        if self._is_loopback():
            return True
        if not self.server.allow_remote:
            return False
        nets = self.server.allow_cidr
        if not nets:
            return True
        try:
            addr = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(addr in net for net in nets)

    def _authorized(self):
        # 本机（root/运维自己的 shell）免登录；任何远程请求都必须带上账号口令。
        if self._is_loopback():
            return True
        user = self.server.auth_user
        password = self.server.auth_pass
        if not user or not password:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:].strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        got_user, _, got_pass = raw.partition(":")
        return hmac.compare_digest(got_user, user) and hmac.compare_digest(got_pass, password)

    def _challenge(self):
        data = json.dumps({"success": False, "error": "需要登录管理界面"}, ensure_ascii=False).encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ST-MCP admin", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        if not self._remote_ok():
            self._send(403, json.dumps({"success": False, "error": "该来源地址不允许访问管理界面"}, ensure_ascii=False))
            return False
        if not self._authorized():
            self._challenge()
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/info":
            return self._send(200, json.dumps(mode_info(), ensure_ascii=False))
        if path == "/api/list":
            try:
                tenants = read_registry()
                return self._send(200, json.dumps({"tenants": tenants}, ensure_ascii=False))
            except (OSError, ValueError) as exc:
                return self._send(
                    200,
                    json.dumps({"tenants": [], "warning": "注册表读取失败：%s" % exc}, ensure_ascii=False),
                )
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/create":
            tenant = str(body.get("tenant") or "").strip()
            args = ["-t", tenant, "--password-stdin"]
            for key, flag in (("db_user", "-u"), ("db_host", "-H"), ("db_port", "-N"),
                              ("db_name", "-d"), ("server_ip", "-I")):
                val = str(body.get(key) or "").strip()
                if val:
                    args += [flag, val]
            if str(body.get("access_mode") or "") == "restricted":
                args.append("--restricted")
            pw = str(body.get("password") or "").strip()
            code, out, err = run_script(CREATE, args, stdin_text=(pw or "") + "\n")
            data = last_json(out)
            if data is None:
                tail = (err or out).splitlines()[-1] if (err or out) else "无输出"
                data = {"success": False, "error": "开通失败：" + tail[:400]}
            return self._send(200, json.dumps(data, ensure_ascii=False))

        if path == "/api/delete":
            tenant = str(body.get("tenant") or "").strip()
            if not tenant:
                return self._send(200, json.dumps({"success": False, "error": "缺少租户名"}))
            code, out, err = run_script(DELETE, ["-t", tenant], timeout=120)
            data = last_json(out)
            if data is None:
                tail = (err or out).splitlines()[-1] if (err or out) else "无输出"
                data = {"success": False, "error": "删除失败：" + tail[:400]}
            return self._send(200, json.dumps(data, ensure_ascii=False))

        return self._send(404, json.dumps({"error": "not found"}))


class AdminServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 允许两个进程绑同一端口，后启动的会静默失效，
    # 所以这里关掉复用，端口被占时直接报错，而不是看起来"启动成功"。
    allow_reuse_address = os.name != "nt"

    allow_remote = False
    auth_user = ""
    auth_pass = ""
    allow_cidr = ()


def main():
    ap = argparse.ArgumentParser(description="ST-MCP 租户管理界面")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--allow-remote", action="store_true",
                    help="允许非本机访问（远程必须登录；页面含明文口令与 token，慎用）")
    ap.add_argument("--auth-file", default=AUTH_FILE,
                    help="登录配置（ADMIN_USER/ADMIN_PASSWORD/ADMIN_ALLOW_CIDR），默认 secrets/admin.env")
    args = ap.parse_args()

    if not SHELL:
        sys.exit("找不到 sh/bash：请从 Git Bash 或 Linux shell 启动，或设置 ST_ADMIN_SH")

    auth = auth_config(args.auth_file)
    if args.allow_remote and not (auth["user"] and auth["password"]):
        sys.exit("拒绝启动：非本机访问必须配置登录。先执行 sh tools/start-admin.sh 生成 %s，"
                 "或自行写入 ADMIN_USER=… / ADMIN_PASSWORD=…" % args.auth_file)

    os.makedirs(DATA, exist_ok=True)
    try:
        httpd = AdminServer((args.host, args.port), Handler)
    except OSError as exc:
        sys.exit("端口 %d 起不来（%s）。多半是上一次的界面还在跑：换端口 sh tools/start-admin.sh 8002" % (args.port, exc))
    httpd.allow_remote = args.allow_remote
    httpd.auth_user = auth["user"]
    httpd.auth_pass = auth["password"]
    httpd.allow_cidr = parse_cidrs(auth["allow_cidr"])
    info = mode_info()
    print("ST-MCP 租户管理已启动")
    print("  访问地址：http://%s:%d" % (args.host, args.port))
    if auth["user"]:
        print("  远程登录：%s（口令在 %s）" % (auth["user"], auth["file"]))
    else:
        print("  远程登录：未配置（只允许本机访问）")
    if httpd.allow_cidr:
        print("  来源限制：%s" % ", ".join(str(n) for n in httpd.allow_cidr))
    print("  MCP 服务：%s（%d 个租户）" % (info["base_url"], info["tenants"]))
    print("  注册表：%s" % info["registry"])
    print("  运行状态：%s" % {
        "real": "单端口多租户",
        "old-image": "容器仍是旧镜像（需重跑 install-all.sh）",
        "preview": "预览（没有 docker，只写注册表）",
    }.get(info["deploy_mode"], info["deploy_mode"]))
    print("  停止：Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

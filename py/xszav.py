# -*- coding: utf-8 -*-
"""
XSZAV（cn.xsz-av.com）TVBox / FongMi Python Spider
==================================================
单文件爬虫，裸类 Spider、无参实例化，13 个接口方法齐备。

站点结构（实测）
---------------
  首页          https://cn.xsz-av.com/
  分类          /videos/<类别>            翻页 ?page=N（N 从 1 起）
                类别严格取首页「类别」下拉里的 8 项：
                日本AV / 无码影片 / 无码流出 / 素人 / 中国 / 韩国 / 欧美 / 动漫
  搜索          /search/videos/<关键词>?page=N
  详情          /video/<数字ID>
  母带索引      详情页内 <video src="/media/v_<hash>.m3u8">（站内相对路径）
                母带里是标准多码率清单，常见档位 1080p / 720p / 480p
  分片 CDN      https://v1.xsz2-cdn.com/v4/<ID>_1080p/v.m3u8 之类

两处需要留意的实测结论
--------------------
1) 防盗链（三项实测都要）
   站内 /media/*.m3u8 只认 Referer；分片 CDN 要求 Referer + Origin + 非空 UA，
   少任何一项直接 403（空 UA、Python-urllib 的 UA 同样 403）。
   播放器一般只带 UA，把 Referer / Origin 丢掉——这就是「列表正常、一点播放
   就黑屏」的根因。本爬虫内置本地中继（127.0.0.1 随机端口）统一补头，并在
   把地址交给播放器前先自检（真取到播放列表 + 1KB 分片才算通），不过就退直连。
   三条路可选（extend 里配 play / media），详见随包交付说明。

2) 分片伪装
   分片文件名是 vN.jpeg、响应头也写 image/jpeg，实际字节是 MPEG-TS
   （首字节 0x47）。中继会把 MIME 纠正成 video/mp2t，否则部分播放器
   会因为格式判定失败直接不播。

3) 主站请求
   主站前置了云端防护，会对 TLS 指纹做校验：普通 urllib / requests 即使
   带完整浏览器头也会被 403。因此传输层按 curl_cffi → requests → urllib
   依次尝试，curl_cffi 命中时最稳。分片 CDN 不做这层校验，普通请求即可。

清晰度策略
---------
默认取最高：有 1080P 就给 1080P，没有就退到该片自身的最高档
（部分老片/其他分区最高只有 720P）。选集里同时列出所有可用档位，可手选。
"""

import html as _htmlmod
import hashlib
import os
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import quote, unquote, urljoin, urlparse, parse_qs

# ── 传输层依赖（都可选，按可用性降级） ──────────────────────────────────────
try:
    from curl_cffi import requests as _cffi_requests
except Exception:
    _cffi_requests = None

try:
    import requests as _requests
except Exception:
    _requests = None

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except Exception:                                   # pragma: no cover
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import socketserver

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True


# ══════════════════════════════════════════════════════════════════════════
# 卷一  站点常量
# ══════════════════════════════════════════════════════════════════════════

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 用户指定的站点地址；init() 里可通过 extend 覆盖
_SITE = "https://cn.xsz-av.com"

_TIMEOUT = 20
_DETAIL_TIMEOUT = 25

# 首页「类别」下拉里的 8 个分类，顺序与站点一致
_CLASSES = (
    ("日本AV", "japanese"),
    ("无码影片", "uncensored"),
    ("无码流出", "uncensored-leak"),
    ("素人", "amateur"),
    ("中国", "chinese"),
    ("韩国", "korean"),
    ("欧美", "western"),
    ("动漫", "hanime"),
)

_CLASS_MAP = dict(_CLASSES)

# curl_cffi 的浏览器指纹候选（不同版本可用值不同，逐个退）
_IMPERSONATE = ("chrome124", "chrome120", "chrome", "edge101", "safari17_0")


# ══════════════════════════════════════════════════════════════════════════
# 卷二  HTTP 层（多通道自适应 · 专治 Cloudflare Managed Challenge）
# ══════════════════════════════════════════════════════════════════════════
#
# 为什么原来的写法过不去，实测结论（同一出口、同一秒、同一 URL）：
#
#   curl_cffi(impersonate=chrome)              -> 200 真页面
#   curl 命令行（浏览器头给全）                  -> 403 challenge
#   python urllib / http.client                -> 403 challenge
#   python urllib + 改成 Chrome 的 cipher 顺序   -> 403 challenge
#   JVM HttpURLConnection（还带了 Android UA）  -> 403 challenge
#   curl_cffi 只留 TLS 指纹、把 UA 清空          -> 403 challenge
#
# 也就是说：盾判的不是 IP、不是 cookie，而是「UA 和 TLS 指纹配不配套」。
# 只要 UA 说是 Chrome、TLS 却是 Python/JVM 的，就一定吃挑战；
# 反过来指纹是 Chrome、UA 对不上，也一样吃挑战。
# 所以「urllib 多加点 header」这条路根本走不通，必须换通道。
#
# 本层做四件事：
#   1) 多通道阶梯：谁能过就用谁，并且按战绩自动排序
#   2) 挑战页识别：403 + cf-mitigated + "Just a moment" 这种直接判失败，换下一层
#   3) 门票复用：一旦拿到 cf_clearance 就落盘，之后所有通道都带上
#   4) 全挂时给明确信号，让上层提示用户，而不是丢一个空列表
#
# 通道优先级（从最可能过盾往下退）：
#   curl_cffi   —— 完整浏览器 TLS + HTTP2 指纹，实测 200
#   java-okhttp —— 走 Android 的 Conscrypt(BoringSSL)，与 Chrome 同源，壳里最靠谱
#   java-urlconn—— 同上的另一条 JVM 路径
#   requests    —— 壳里若带就试
#   urllib      —— 纯标准库兜底
#
# 两条独立自救线（extend 里配一项即可，见 init）：
#   relay  : 自己的过盾中转地址，例如 "https://你的域名/unlock?u="
#   ticket : 手工填 cf_clearance（手机浏览器过一次盾后抄出来）

_UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# 和 Android 原生 TLS 栈（Conscrypt / BoringSSL）最配套的 UA
_UA_ANDROID = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# curl_cffi 的浏览器指纹候选（不同版本可用值不同，逐个退）
_IMPERSONATE = ("chrome131", "chrome124", "chrome120", "chrome", "edge101", "safari17_0")

# 挑战页特征
_CH_MARKS = (b"Just a moment", b"_cf_chl_opt", b"Enable JavaScript and cookies",
             b"challenge-platform", b"cf-challenge")


def _site():
    return _SITE.rstrip("/")


def _site_home():
    return _site() + "/"


def _headers(referer=None, want_origin=False, accept=None, ua=None):
    h = {
        "Accept": accept or ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                             "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if ua:
        h["User-Agent"] = ua
    if referer:
        h["Referer"] = referer
    if want_origin:
        h["Origin"] = _site_home().rstrip("/")
    return h


# ── 挑战识别 ────────────────────────────────────────────────────────────────

def _looks_challenge(body):
    if not body:
        return False
    head = body[:9000] if isinstance(body, bytes) else str(body)[:9000].encode("utf-8", "ignore")
    return any(m in head for m in _CH_MARKS)


def _is_challenge(status, hdrs, body):
    """真页面 vs 盾页。只有明确是盾才算，避免把 404 误判。"""
    hdrs = hdrs or {}
    try:
        if hdrs.get("cf-mitigated"):
            return True
    except Exception:
        pass
    if int(status or 0) in (403, 429, 503, 520, 521, 522, 523, 524):
        return _looks_challenge(body)
    # 少数情况盾返回 200（比如 JS 挑战壳），但内容一眼能认
    if int(status or 0) == 200 and body and len(body) < 20000:
        return _looks_challenge(body)
    return False


# ── 通道战绩 ────────────────────────────────────────────────────────────────

_CH_LOCK = threading.Lock()
_CH_STAT = {}                       # name -> [ok, fail]
_BASE_ORDER = ("curl_cffi", "java-okhttp", "java-urlconn", "requests", "urllib")
_CH_LAST = {}                       # name -> 最近一次结果摘要（自检用）
_BLOCKED = [0]                      # 全通道都被盾拦住的次数


def _ch_ok(name):
    with _CH_LOCK:
        s = _CH_STAT.setdefault(name, [0, 0])
        s[0] += 1
        _CH_LAST[name] = "ok"


def _ch_fail(name, why="challenge"):
    with _CH_LOCK:
        s = _CH_STAT.setdefault(name, [0, 0])
        s[1] += 1
        _CH_LAST[name] = why


def _channel_order():
    """按战绩排序；连续失败且从未成功过的通道直接跳过，别每次白等一轮超时。"""
    out = []
    for n in _BASE_ORDER:
        ok, fail = _CH_STAT.get(n, (0, 0))
        if fail >= 4 and ok == 0:
            continue
        out.append(n)
    if not out:
        out = ["urllib"]

    def key(n):
        try:
            idx = _BASE_ORDER.index(n)
        except ValueError:
            idx = 9
        ok, fail = _CH_STAT.get(n, (0, 0))
        return (1 if (fail > 0 and ok == 0) else 0, idx)
    return sorted(out, key=key)


# ── 门票（cf_clearance）缓存 ─────────────────────────────────────────────────
#
# 盾过了之后浏览器会拿到 cf_clearance，一段时间内拿着它就不再被挑战。
# 但它是绑 IP + UA 的：只要手机和浏览器同一个出口、UA 一致，就能复用。
# 所以拿到就落盘，下次直接带；用户也可以手工填进 extend。

_TICKET = {}
_TICKET_LOCK = threading.Lock()
_TICKET_CFG = {}
_TICKET_FILE = [None]
_TICKET_KEYS = ("cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog")


def _ticket_file():
    if _TICKET_FILE[0] is not None:
        return _TICKET_FILE[0]
    cands = []
    try:
        import tempfile
        cands.append(os.path.join(tempfile.gettempdir(), "xszav_ticket.json"))
    except Exception:
        pass
    try:
        cands.append(os.path.join(os.path.expanduser("~"), ".xszav_ticket.json"))
    except Exception:
        pass
    _TICKET_FILE[0] = cands[0] if cands else ""
    return _TICKET_FILE[0]


def _load_ticket():
    path = _ticket_file()
    if not path:
        return
    try:
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _TICKET_LOCK:
                for k, v in data.items():
                    if k in _TICKET_KEYS and v:
                        _TICKET[k] = str(v)
    except Exception:
        pass


def _save_ticket():
    path = _ticket_file()
    if not path:
        return
    try:
        with _TICKET_LOCK:
            data = dict(_TICKET)
        if not data:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _absorb_cookie(hdrs):
    """从响应头里捞 cf_clearance 之类的门票。"""
    if not hdrs:
        return
    raw = ""
    try:
        raw = hdrs.get("set-cookie") or hdrs.get("Set-Cookie") or ""
    except Exception:
        return
    if not raw:
        return
    changed = False
    for piece in str(raw).split(","):
        kv = piece.split(";", 1)[0].strip()
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in _TICKET_KEYS and v and len(v) > 8:
            with _TICKET_LOCK:
                if _TICKET.get(k) != v:
                    _TICKET[k] = v
                    changed = True
    if changed:
        _save_ticket()


def _cookie_header():
    with _TICKET_LOCK:
        pairs = ["%s=%s" % (k, v) for k, v in _TICKET.items() if v]
    return "; ".join(pairs)


def _set_ticket(cf_clearance="", ua=""):
    """外部设置门票：cf_clearance 值；ua 要跟拿票时浏览器一致。"""
    global _UA_FOR_TICKET
    if cf_clearance:
        with _TICKET_LOCK:
            _TICKET["cf_clearance"] = str(cf_clearance).strip()
        _save_ticket()
    if ua:
        _UA_FOR_TICKET = str(ua).strip()


_UA_FOR_TICKET = ""


# ── 中转（可选，最省事的一条过盾线） ────────────────────────────────────────

_RELAY_UPSTREAM = [""]


def _via_upstream(url):
    """只有主站（被盾保护的那部分）才走中转；CDN 分片本来不过盾，直连更快更省。"""
    base = _RELAY_UPSTREAM[0]
    if not base or not url:
        return url
    try:
        if not _is_site_host(url):
            return url
    except Exception:
        pass
    if base.endswith("?u=") or base.endswith("="):
        return base + quote(url, safe="")
    return base.rstrip("/") + "/" + quote(url, safe="")


# ── 各通道实现 ─────────────────────────────────────────────────────────────

_CFFI_LOCAL = threading.local()


def _cffi_session():
    sess = getattr(_CFFI_LOCAL, "s", None)
    if sess is not None:
        return sess
    sess = None
    for imp in _IMPERSONATE:
        try:
            sess = _cffi_requests.Session(impersonate=imp)
            break
        except Exception:
            sess = None
    if sess is None:
        try:
            sess = _cffi_requests.Session()
        except Exception:
            return None
    _CFFI_LOCAL.s = sess
    return sess


def _chan_curl_cffi(url, hdr, timeout):
    """完整浏览器指纹。UA 交给 curl_cffi 自己填，配不上反而会被挑战。"""
    if _cffi_requests is None:
        return None
    sess = _cffi_session()
    if sess is None:
        return None
    h = dict(hdr or {})
    h.pop("User-Agent", None)
    try:
        r = sess.get(url, headers=h, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    rh = {}
    try:
        for k, v in r.headers.items():
            rh[str(k).lower()] = str(v)
    except Exception:
        pass
    return int(r.status_code), rh.get("content-type", ""), (r.content or b""), rh


def _java_map(d):
    from java.util import HashMap
    m = HashMap()
    for k, v in (d or {}).items():
        try:
            m.put(str(k), str(v))
        except Exception:
            pass
    return m


def _chan_java_okhttp(url, hdr, timeout):
    """走 Android 原生 TLS 栈（Conscrypt = BoringSSL，与 Chrome 同源）。"""
    # A. TVBox 内核自带 OkHttp
    try:
        from com.github.catvod.net import OkHttp as _CvOk
        txt = _CvOk.string(url, _java_map(hdr))
        if txt is not None:
            body = txt.encode("utf-8", "ignore") if isinstance(txt, str) else bytes(txt)
            if body:
                st = 403 if _looks_challenge(body) else 200
                return st, "text/html", body, {}
    except Exception:
        pass
    # B. okhttp3 原生
    try:
        from okhttp3 import OkHttpClient, Request
        from java.util.concurrent import TimeUnit
        bld = OkHttpClient.Builder()
        bld.connectTimeout(int(timeout), TimeUnit.SECONDS)
        bld.readTimeout(int(timeout), TimeUnit.SECONDS)
        try:
            bld.followRedirects(True)
            bld.followSslRedirects(True)
        except Exception:
            pass
        cli = bld.build()
        rb = Request.Builder().url(url)
        for k, v in (hdr or {}).items():
            try:
                rb.addHeader(str(k), str(v))
            except Exception:
                pass
        resp = cli.newCall(rb.build()).execute()
        code = int(resp.code())
        rh = {}
        try:
            hs = resp.headers()
            for name in hs.names():
                rh[str(name).lower()] = str(hs.get(str(name)))
        except Exception:
            pass
        raw = b""
        try:
            bd = resp.body()
            if bd is not None:
                raw = bytes(bd.bytes())
        except Exception:
            raw = b""
        try:
            resp.close()
        except Exception:
            pass
        return code, rh.get("content-type", ""), raw, rh
    except Exception:
        pass
    return None


def _chan_java_urlconn(url, hdr, timeout):
    """JVM 另一条路径，某些壳没暴露 okhttp3 时顶上。"""
    try:
        from java.net import URL as _JURL
    except Exception:
        return None
    try:
        conn = _JURL(url).openConnection()
        conn.setConnectTimeout(int(timeout) * 1000)
        conn.setReadTimeout(int(timeout) * 1000)
        try:
            conn.setInstanceFollowRedirects(True)
        except Exception:
            pass
        for k, v in (hdr or {}).items():
            try:
                conn.setRequestProperty(str(k), str(v))
            except Exception:
                pass
        code = int(conn.getResponseCode())
        rh = {}
        try:
            for i in range(0, 80):
                k = conn.getHeaderFieldKey(i)
                v = conn.getHeaderField(i)
                if k is None and v is None:
                    break
                if k:
                    rh[str(k).lower()] = str(v)
        except Exception:
            pass
        st = None
        try:
            st = conn.getInputStream() if code < 400 else conn.getErrorStream()
        except Exception:
            st = None
        buf = bytearray()
        if st is not None:
            try:
                chunk = bytearray(16384)
                while True:
                    n = st.read(chunk)
                    if not n or n < 0:
                        break
                    buf.extend(bytes(chunk[:n]))
                    if len(buf) > 8 * 1024 * 1024:
                        break
            except Exception:
                pass
            try:
                st.close()
            except Exception:
                pass
        return code, rh.get("content-type", ""), bytes(buf), rh
    except Exception:
        return None


_REQ_LOCAL = threading.local()


def _chan_requests(url, hdr, timeout):
    if _requests is None:
        return None
    sess = getattr(_REQ_LOCAL, "s", None)
    if sess is None:
        try:
            sess = _requests.Session()
            _REQ_LOCAL.s = sess
        except Exception:
            return None
    try:
        r = sess.get(url, headers=hdr, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    rh = {}
    try:
        for k, v in r.headers.items():
            rh[str(k).lower()] = str(v)
    except Exception:
        pass
    return int(r.status_code), rh.get("content-type", ""), (r.content or b""), rh


def _chan_urllib(url, hdr, timeout):
    got = _urllib_get(url, hdr, timeout)
    st, ct, body = got
    if st == 0:
        return None
    return st, ct, body, {}


_CHANNELS = {
    "curl_cffi": _chan_curl_cffi,
    "java-okhttp": _chan_java_okhttp,
    "java-urlconn": _chan_java_urlconn,
    "requests": _chan_requests,
    "urllib": _chan_urllib,
}


# ── 会话（兼容老接口） ─────────────────────────────────────────────────────

_LOCAL = threading.local()


def _build_session():
    """给老调用点留的兼容函数。真正发请求走 _http_get 的通道阶梯。"""
    if _cffi_requests is not None:
        return _cffi_session()
    if _requests is not None:
        try:
            s = _requests.Session()
            s.headers.update(_headers(ua=_UA_ANDROID))
            return s
        except Exception:
            return None
    return None


def _open_session():
    sess = getattr(_LOCAL, "sess", None)
    if sess is None:
        sess = _build_session()
        _LOCAL.sess = sess
    return sess


def _session_get(url, hdr, timeout):
    """老接口：走 curl_cffi/requests 会话。"""
    sess = _open_session()
    if sess is None:
        return None
    try:
        r = sess.get(url, headers=hdr, timeout=timeout, allow_redirects=True)
        return r.status_code, (r.headers.get("Content-Type") or ""), r.content
    except Exception:
        return None


def _urllib_get(url, hdr, timeout):
    req = urllib.request.Request(url, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (r.headers.get("Content-Type") or ""), r.read()
    except urllib.error.HTTPError as e:
        hdrs = e.headers
        body = b""
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return e.code, ((hdrs.get("Content-Type") or "") if hdrs else ""), body
    except Exception:
        return 0, "", b""


# ── 主入口：多通道阶梯 ─────────────────────────────────────────────────────

def _http_get(url, referer=None, want_origin=False, timeout=None, accept=None, raw_url=None, extra=None):
    """GET → (status, content_type, body_bytes)。

    按通道优先级逐个试，第一个拿到「非盾页」的直接返回；
    全是盾就返回最后一次的盾页（上层据此提示用户）。
    """
    timeout = timeout or _TIMEOUT
    target = _via_upstream(raw_url or url)
    cookie = _cookie_header()
    last = (0, "", b"")
    saw_challenge = False

    for name in _channel_order():
        fn = _CHANNELS.get(name)
        if fn is None:
            continue
        hdr = _headers(referer=referer, want_origin=want_origin, accept=accept,
                       ua=(None if name == "curl_cffi" else (_UA_FOR_TICKET or _UA_ANDROID)))
        if cookie:
            hdr["Cookie"] = cookie
        if extra:
            hdr.update(extra)
        try:
            res = fn(target, hdr, timeout)
        except Exception:
            res = None
        if not res:
            continue
        st, ct, body, rh = res
        if _is_challenge(st, rh, body):
            saw_challenge = True
            _ch_fail(name, "challenge")
            last = (st, ct, body)
            continue
        if st == 0:
            _ch_fail(name, "neterr")
            continue
        _ch_ok(name)
        _absorb_cookie(rh)
        return st, ct, body

    if saw_challenge:
        with _CH_LOCK:
            _BLOCKED[0] += 1
    return last


class ShieldBlocked(RuntimeError):
    """所有通道都被 Cloudflare 盾拦住。重试没有意义，直接往上抛。"""


def _get_text(url, referer=None, timeout=None):
    st, ct, body = _http_get(url, referer=referer, timeout=timeout)
    if st >= 400 or not body:
        if _looks_challenge(body):
            raise ShieldBlocked("被 CF 盾拦住(HTTP %s): %s" % (st, url))
        raise RuntimeError("HTTP %s for %s" % (st, url))
    enc = "utf-8"
    m = re.search(r"charset=([\w\-]+)", ct or "", re.I)
    if m:
        enc = m.group(1)
    try:
        return body.decode(enc, "ignore")
    except Exception:
        return body.decode("utf-8", "ignore")


def _get_text_retry(url, referer=None, timeout=None, attempts=3):
    last = None
    for i in range(attempts):
        try:
            txt = _get_text(url, referer=referer, timeout=timeout)
            if txt and len(txt) > 400:
                return txt
            last = RuntimeError("short body %d" % len(txt or ""))
        except ShieldBlocked:
            raise
        except Exception as e:
            last = e
        time.sleep(0.4 * (i + 1))
    raise last if last else RuntimeError("fetch failed")


# ── 通道自检（给 __main__ 和排障用） ────────────────────────────────────────

# ── 全通道被拦时的提示（别让壳里只剩一个空列表） ──────────────────────────

_BLOCKED_ID = "__cf_blocked__"


def _blocked_item():
    return {
        "vod_id": _BLOCKED_ID,
        "vod_name": "⛔ 被 Cloudflare 盾拦住了 —— 在源配置里填 relay 中转即可（见交付说明）",
        "vod_pic": "",
        "vod_remarks": "点我",
    }


def _blocked_detail():
    return {
        "vod_id": _BLOCKED_ID,
        "vod_name": "过盾解法（点开看）",
        "vod_pic": "",
        "vod_content": (
            "这个源被 Cloudflare Managed Challenge 挡住了。壳里的 Python 发不出浏览器指纹，"
            "所以拿不到页面。\n\n"
            "解法一（最稳）：自己有服务器的话，跑交付里的 unlock_server.py，"
            "再把源配置改成 {\"host\":\"https://cn.xsz-av.com\","
            "\"relay\":\"http://你的IP:8787/u?k=口令&u=\"}。\n\n"
            "解法二：手机浏览器打开站点过一次盾，把 cf_clearance 的值填进配置的 ticket 里，"
            "ua 填那台浏览器的 UA。\n\n"
            "想先看你这台机器哪条通道能过，就把这个 py 单独跑一次，"
            "输出里有一行「通道体检」。"),
        "vod_remarks": "盾拦说明",
        "vod_play_from": "说明",
        "vod_play_url": "点我$%s|info" % _BLOCKED_ID,
    }


def _maybe_hinted(items):
    """列表为空、且这一轮确实吃过盾，就插一条提示，让用户知道不是源坏了。"""
    if items:
        return items
    with _CH_LOCK:
        n = _BLOCKED[0]
    return [_blocked_item()] if n > 0 else items


def _probe_channels(url=None):
    """逐个通道打一次首页，返回 [(通道, 结果)]，不写缓存、不改战绩排序。"""
    url = url or (_site_home())
    out = []
    cookie = _cookie_header()
    for name in _BASE_ORDER:
        fn = _CHANNELS.get(name)
        if fn is None:
            out.append((name, "未安装"))
            continue
        hdr = _headers(ua=(None if name == "curl_cffi" else (_UA_FOR_TICKET or _UA_ANDROID)))
        if cookie:
            hdr["Cookie"] = cookie
        try:
            res = fn(url, hdr, 20)
        except Exception as e:
            out.append((name, "异常 %s" % type(e).__name__))
            continue
        if not res:
            out.append((name, "不可用"))
            continue
        st, ct, body, rh = res
        if _is_challenge(st, rh, body):
            out.append((name, "被盾拦(HTTP %s)" % st))
        elif st == 200 and body:
            out.append((name, "通过(HTTP 200, %d 字节)" % len(body)))
        else:
            out.append((name, "HTTP %s" % st))
    return out
# ══════════════════════════════════════════════════════════════════════════
# 卷三  文本工具
# ══════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(s):
    if not s:
        return ""
    s = _htmlmod.unescape(s)
    s = _TAG_RE.sub("", s)
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    return _WS_RE.sub(" ", s).strip()


def _fix_url(u):
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(_site_home(), u)


def _video_url(vid):
    return "%s/video/%s" % (_site(), quote(str(vid).strip().strip("/"), safe=""))


def _list_url(cat, pg):
    return "%s/videos/%s?page=%d" % (_site(), str(cat).strip("/"), int(pg))


def _search_url(kw, pg):
    return "%s/search/videos/%s?page=%d" % (_site(), quote(kw, safe=""), int(pg))


# ══════════════════════════════════════════════════════════════════════════
# 卷四  列表页解析
# ══════════════════════════════════════════════════════════════════════════

# 卡片容器；站点是 SSR，卡片结构稳定
_CARD_SPLIT = '<div class="relative aspect-w-16 aspect-h-9'
_IMG_RE = re.compile(
    r'(?:data-src|src)="(https?://(?:img|i\d)\.xszcdn\.net/[^"]+?\.(?:jpg|jpeg|png|webp))"', re.I)
_ALT_RE = re.compile(r'alt="([^"]*)"')
_DUR_RE = re.compile(r'<span class="absolute bottom-1 right-1[^"]*">\s*([\d:]{4,10})\s*</span>')
_TITLE_RE = re.compile(r'line-clamp-1[^>]*>\s*([^<]{1,400}?)</a>')


def _cover_big(u):
    """列表封面是 w_330 缩略图，放大一档，TVBox 里更清楚。"""
    if not u:
        return ""
    return re.sub(r"/w_\d+/", "/w_800/", u)


def _parse_list(html):
    """列表页 → [vod dict]（按 vod_id 去重，保持站点顺序）。"""
    items = []
    seen = set()
    if not html:
        return items

    chunks = html.split(_CARD_SPLIT)
    if len(chunks) <= 1:
        # 兜底：首页等非标准卡片布局，直接按详情链接切
        chunks = html.split('href="/video/')

    for chunk in chunks[1:]:
        try:
            chunk = chunk[:3000]

            vm = re.search(r'/video/(\d+)', chunk)
            if vm:
                vid = vm.group(1)
            elif chunks is not None and chunk[:1].isdigit():
                vid = re.match(r'(\d+)', chunk).group(1)
            else:
                continue
            if not vid or vid in seen:
                continue

            pic = ""
            pm = _IMG_RE.search(chunk)
            if pm:
                pic = _cover_big(pm.group(1))

            title = ""
            tm = _TITLE_RE.search(chunk)
            if tm:
                title = _clean(tm.group(1))
            if not title:
                am = _ALT_RE.search(chunk)
                if am:
                    title = _clean(am.group(1))
            if not title:
                title = vid

            dur = ""
            dm = _DUR_RE.search(chunk)
            if dm:
                dur = dm.group(1)

            seen.add(vid)
            items.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": dur,
            })
        except Exception:
            continue
    return items


def _parse_pagecount(html, pg):
    """从分页控件推总页数。"""
    try:
        m = re.findall(r'aria-label="Go to page (\d+)"', html or "")
        if m:
            return max(int(x) for x in m)
        m = re.findall(r'[?&]page=(\d+)', html or "")
        if m:
            return max(int(x) for x in m)
    except Exception:
        pass
    try:
        return int(pg)
    except Exception:
        return 1


# ══════════════════════════════════════════════════════════════════════════
# 卷五  详情页解析
# ══════════════════════════════════════════════════════════════════════════

_META_RE = re.compile(r'<meta[^>]+(?:property|name)="([^"]+)"[^>]+content="([^"]*)"', re.I)
# 详情信息行：<span class="mr-1">添加日期:</span><span class="font-medium">2026-09-20</span>
_FIELD_RE = re.compile(
    r'<span class="mr-1">\s*([^<:：]{1,10})\s*[:：]\s*</span>\s*'
    r'<span class="font-medium">(.*?)</span>', re.S)
# 站内播放器：<video src="/media/v_xxx.m3u8">
_PLAYER_RE = re.compile(r'<video[^>]+src="(/media/[^"]+?\.m3u8)"', re.I)
_ANY_M3U8_RE = re.compile(r'(/media/v_[0-9a-fA-F]+\.m3u8)')
_ACTOR_RE = re.compile(r'href="/(?:actresses|actress)/[^"]+"[^>]*>([^<]{1,60})</a>')


def _parse_detail(html, vid):
    """详情页 → vod dict（字段缺失就留空，不编造）。"""
    meta = {}
    for k, v in _META_RE.findall(html or ""):
        meta.setdefault(k.lower(), _htmlmod.unescape(v))

    title = _clean(meta.get("og:title") or "")
    if not title:
        tm = re.search(r"<title>(.*?)</title>", html or "", re.S)
        if tm:
            title = _clean(tm.group(1)).split(" - ")[0].strip()
    if not title:
        hm = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S)
        title = _clean(hm.group(1)) if hm else str(vid)

    pic = meta.get("og:image") or ""
    if not pic:
        pm = _IMG_RE.search(html or "")
        pic = pm.group(1) if pm else ""
    pic = _cover_big(pic)

    desc = _clean(meta.get("og:description") or meta.get("description") or "")

    fields = {}
    for km in _FIELD_RE.finditer(html or ""):
        key = _clean(km.group(1))
        val = _clean(km.group(2))
        if key and val and key not in fields:
            fields[key] = val

    add_date = fields.get("添加日期") or fields.get("添加時間") or ""
    code = fields.get("番号") or fields.get("番號") or ""
    tags = fields.get("类型") or fields.get("類型") or fields.get("标签") or fields.get("標籤") or ""
    label = fields.get("片商") or fields.get("发行商") or fields.get("發行商") or ""

    # 类型/标签有时用链接列出，文本抽不全时从链接兜一次
    if not tags:
        tlinks = []
        for m in re.finditer(r'href="/search/videos/[^"]+"[^>]*>([^<]{1,60})</a>', html or ""):
            t = _clean(m.group(1))
            if t and t not in tlinks:
                tlinks.append(t)
        tags = ", ".join(tlinks[:12])

    actors = []
    for m in _ACTOR_RE.finditer(html or ""):
        a = _clean(m.group(1))
        if a and a not in actors:
            actors.append(a)

    year = ""
    ym = re.search(r"(\d{4})-\d{2}-\d{2}", add_date)
    if ym:
        year = ym.group(1)

    remarks = code or ""
    if label and label not in remarks:
        remarks = (remarks + " " + label).strip()

    return {
        "vod_id": str(vid),
        "vod_name": title,
        "vod_pic": pic,
        "vod_content": desc,
        "vod_actor": ", ".join(actors[:8]),
        "vod_director": "",
        "vod_area": "",
        "vod_lang": "",
        "vod_year": year,
        "vod_pubdate": add_date,
        "vod_remarks": remarks or (add_date or ""),
        "vod_tag": tags,
    }


def _extract_master(html):
    """详情页 → 母带 m3u8 绝对地址。"""
    m = _PLAYER_RE.search(html or "")
    if not m:
        m = _ANY_M3U8_RE.search(html or "")
    if not m:
        return ""
    return _fix_url(m.group(1))


def _parse_variants(text, base_url):
    """母带 → {height: 绝对地址}。"""
    out = {}
    lines = [l.strip() for l in (text or "").splitlines()]
    for i, ln in enumerate(lines):
        if not ln.upper().startswith("#EXT-X-STREAM-INF"):
            continue
        j = i + 1
        while j < len(lines) and (not lines[j] or lines[j].startswith("#")):
            j += 1
        if j >= len(lines):
            continue
        uri = urljoin(base_url, lines[j])
        m = re.search(r"RESOLUTION=\d+x(\d+)", ln, re.I)
        if m:
            h = int(m.group(1))
        else:
            nm = re.search(r'NAME="([^"]+)"', ln, re.I)
            hm = re.search(r"(\d{3,4})", nm.group(1)) if nm else None
            h = int(hm.group(1)) if hm else 0
        if h not in out:
            out[h] = uri
    return out


_PLAY_CACHE = {}
_PLAY_LOCK = threading.Lock()


def _resolve_play(vid, html=None):
    """返回 (母带地址, {height: 地址})，进程内缓存。"""
    key = str(vid)
    with _PLAY_LOCK:
        hit = _PLAY_CACHE.get(key)
    if hit and hit[0]:
        return hit

    if html is None:
        html = _get_text_retry(_video_url(vid), referer=_site_home(), timeout=_DETAIL_TIMEOUT)

    master = _extract_master(html)
    variants = {}
    if master:
        try:
            st, ct, body = _http_get(master, referer=_video_url(vid), timeout=20)
            if st < 400 and body:
                variants = _parse_variants(body.decode("utf-8", "ignore"), master)
        except Exception:
            variants = {}

    res = (master, variants)
    with _PLAY_LOCK:
        _PLAY_CACHE[key] = res
    return res


def _pick_target(vid, want="auto"):
    """按清晰度取播放地址；auto = 最高（优先 1080P，没有就取本站最高档）。"""
    master, variants = _resolve_play(vid)
    heights = sorted([h for h in variants.keys() if isinstance(h, int) and h > 0], reverse=True)

    if not variants:
        return master, heights

    if isinstance(want, int):
        if want in variants:
            return variants[want], heights
        best = min(heights, key=lambda h: abs(h - want)) if heights else None
        if best is not None:
            return variants[best], heights

    for h in (1080, 720):
        if h in variants:
            return variants[h], heights
    return variants[heights[0]] if heights else master, heights


# ══════════════════════════════════════════════════════════════════════════
# 卷六  本地中继 v2（补 Referer/Origin + 纠正分片 MIME + Range + 播放自检）
# ══════════════════════════════════════════════════════════════════════════
#
# 为什么必须有这一层（实测）：
#   分片 CDN 对 Referer + Origin + 非空浏览器 UA 三项同时校验，
#   缺任何一个直接 403。播放器（尤其 ijk、外部播放器）通常只带 UA，
#   把 Referer / Origin 丢掉，于是「列表详情全正常，一点播放就黑屏」。
#   中继在插件进程里把这三个头补齐，播放器只要访问 127.0.0.1 就行。
#
# v2 改了什么（都是这一轮真测出来的）：
#   1) 播放列表按 token 相对路径重写，列表体积 310KB -> 192KB，起播更快；
#   2) 支持 Range(206) 与真实 HEAD —— 上一版对任何 Range/HEAD 都回 200 空包，
#      播放器探测一步就判「不可播放」，这是黑屏的主因之一；
#   3) 只解码一次 —— 上一版 parse_qs 解一次、再 unquote 一次，带 % 的地址会被改坏；
#   4) 播放列表 8 秒缓存，播放器连续请求不再重复回源；
#   5) 新增播放自检：插件内部真走一遍「中继 → 播放列表 → 前 1KB 分片」，
#      读到真 TS 字节才把中继地址交给播放器；自检不过自动退直连，
#      不再出现「地址给出去了、其实取不到」的假成功。

_RELAY_LOCK = threading.Lock()
_RELAY = None                  # (server, port)
_RELAY_TRIED = False
_RELAY_HITS = [0, 0]

_MEDIA_TIMEOUT = 25
_PL_CACHE = {}                 # 播放列表地址 -> (时间戳, 文本)
_PL_TTL = 8.0
_BASE_MAP = {}                 # token -> 该播放列表的基准目录
_SELFTEST = {}                 # 分片地址 -> (时间戳, 是否通过, 说明)

_PLAY_MODE = ["auto"]          # auto / relay / direct（extend 里可改）
_MEDIA_UPSTREAM = [""]         # 自己的中转服务（extend 里 media 配），优先于本地中继
_PLAY_NOTE = [""]


def _is_site_host(url):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host.endswith("xsz-av.com")


def _playlist_base(url):
    try:
        return urljoin(url, ".")
    except Exception:
        return url


def _token_for(base):
    try:
        tok = hashlib.md5(base.encode("utf-8")).hexdigest()[:12]
    except Exception:
        tok = "%012x" % (abs(hash(base)) & 0xFFFFFFFFFFFF)
    with _RELAY_LOCK:
        _BASE_MAP[tok] = base
        if len(_BASE_MAP) > 512:
            for k in list(_BASE_MAP.keys())[:128]:
                _BASE_MAP.pop(k, None)
    return tok


def _is_playlist(url, ctype, body):
    if body[:7] == b"#EXTM3U":
        return True
    if "mpegurl" in (ctype or "").lower():
        return True
    return str(url).lower().split("?")[0].endswith(".m3u8")


def _rewrite_tag_line(line, base, prefix):
    def _sub(m):
        u = urljoin(base, m.group(1))
        if u.startswith(base):
            return 'URI="%s/s/%s/%s"' % (prefix, _token_for(base), quote(u[len(base):], safe="/"))
        return 'URI="%s/xsz?u=%s"' % (prefix, quote(u, safe=""))
    return re.sub(r'URI="([^"]+)"', _sub, line)


def _rewrite_m3u8(text, base, prefix):
    """播放列表里的分片一律改写成 127.0.0.1 上的短路径，列表不会变胖。"""
    tok = _token_for(base)
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            out.append(raw)
            continue
        if s.startswith("#"):
            out.append(_rewrite_tag_line(s, base, prefix) if 'URI="' in s else raw)
            continue
        u = urljoin(base, s)
        if u.startswith(base):
            out.append("%s/s/%s/%s" % (prefix, tok, quote(u[len(base):], safe="/")))
        else:
            out.append("%s/xsz?u=%s" % (prefix, quote(u, safe="")))
    return "\n".join(out) + "\n"


def _raw_get(url, hdr, timeout=25):
    """直连取，返回 (status, headers_dict, body)。用于 CDN 分片（不过盾）。"""
    req = urllib.request.Request(url, headers=hdr or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict((k.lower(), v) for k, v in r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return e.code, dict((k.lower(), v) for k, v in (e.headers or {}).items()), body
    except Exception:
        return 0, {}, b""


def _relay_fetch(target, rng=None):
    """回源（补 Referer/Origin）+ 按需重写，返回 (status, ctype, body, extra_headers)。"""
    if _is_site_host(target):
        referer, want_origin = _site_home(), False
    else:
        referer, want_origin = _site_home(), True

    # 分片 CDN 本身支持 Range，直接透传，取 1KB 就只下 1KB（自检和拖动都靠这条）
    if rng and not _is_site_host(target):
        h = {"User-Agent": _UA_ANDROID, "Accept": "*/*", "Referer": referer, "Origin": _site_home().rstrip("/"),
             "Range": rng, "Accept-Language": "zh-CN,zh;q=0.9"}
        st, hd, body = _raw_get(target, h)
        if st in (200, 206) and body:
            ct = "video/mp2t" if body[:1] == b"\x47" else (hd.get("content-type") or "application/octet-stream")
            ex = {}
            if hd.get("content-range"):
                ex["Content-Range"] = hd["content-range"]
            return st, ct, body, ex
        return 502, "text/plain", b"range upstream error", {}

    extra = None
    st, ct, body = 0, "", b""
    for attempt in (0, 1):
        try:
            st, ct, body = _http_get(target, referer=referer, want_origin=want_origin,
                                     timeout=_MEDIA_TIMEOUT, accept="*/*", extra=extra)
        except Exception:
            st, ct, body = 0, "", b""
        if st in (200, 206) and body:
            break
        if st not in (0, 403, 408, 429, 500, 502, 503, 504):
            break
        if attempt == 0:
            time.sleep(0.25)

    if st == 0 or (st >= 400 and not body):
        return 502, "text/plain", b"relay upstream error", {}
    if st >= 400:
        return st, "text/plain", body, {}

    if _is_playlist(target, ct, body):
        prefix = _relay_root()
        if prefix:
            try:
                txt = _rewrite_m3u8(body.decode("utf-8", "ignore"), _playlist_base(target), prefix)
                return 200, "application/vnd.apple.mpegurl", txt.encode("utf-8"), {}
            except Exception:
                return 200, "application/vnd.apple.mpegurl", body, {}
        return 200, "application/vnd.apple.mpegurl", body, {}

    # 分片实际是 MPEG-TS 却顶着 image/jpeg，按首字节纠正，否则播放器判格式失败
    if body[:1] == b"\x47":
        ct = "video/mp2t"
    elif body[:4] == b"\x00\x00\x00\x01" or body[4:8] == b"ftyp":
        ct = "video/mp4"
    else:
        ct = ct or "application/octet-stream"
    return st, ct, body, {}


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, ctype, body, extra=None, clen=None):
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(len(body) if clen is None else clen))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Accept-Ranges", "bytes")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except Exception:
            try:
                self.close_connection = True
            except Exception:
                pass

    def do_HEAD(self):
        self._serve(True)

    def do_GET(self):
        self._serve(False)

    def _serve(self, head):
        _RELAY_HITS[0] += 1
        try:
            p = urlparse(self.path)
            target = ""

            if p.path.startswith("/s/"):
                parts = p.path[3:].split("/", 1)
                tok = parts[0]
                rel = unquote(parts[1]) if len(parts) > 1 else ""
                with _RELAY_LOCK:
                    base = _BASE_MAP.get(tok, "")
                if not base:
                    self._send(404, "text/plain", b"bad token")
                    return
                target = urljoin(base, rel)
            elif p.path.rstrip("/") in ("/xsz", ""):
                q = parse_qs(p.query or "")
                target = unquote((q.get("u") or [""])[0])
            else:
                self._send(404, "text/plain", b"not found")
                return

            if not target.startswith("http"):
                self._send(400, "text/plain", b"bad url")
                return

            with _RELAY_LOCK:
                hit = _PL_CACHE.get(target)
            if hit and time.time() - hit[0] < _PL_TTL:
                self._send(200, "application/vnd.apple.mpegurl", hit[1].encode("utf-8"))
                return

            rng = self.headers.get("Range")
            st, ct, body, hx = _relay_fetch(target, rng=rng)

            if _is_playlist(target, ct, body) and isinstance(body, bytes) and body[:7] == b"#EXTM3U":
                with _RELAY_LOCK:
                    _PL_CACHE[target] = (time.time(), body.decode("utf-8", "ignore"))
                self._send(200, "application/vnd.apple.mpegurl", body)
                return

            if st == 206:
                self._send(206, ct, body, hx)
                return
            if head:
                # HEAD 不能回长度 0，否则播放器探测阶段就判「空资源」。
                # 用 1 字节 Range 回源，从 Content-Range 里拿真实总长。
                st2, ct2, b2, hx2 = _relay_fetch(target, rng="bytes=0-0")
                total = None
                cr = (hx2 or {}).get("Content-Range") or ""
                m = re.match(r"bytes\s+\d+-\d+/(\d+)", cr)
                if m:
                    total = int(m.group(1))
                self._send(st2 if st2 in (200, 206) else 200, ct2, b"", None, total)
                return
            self._send(st, ct, body)
        except Exception:
            try:
                self._send(502, "text/plain", b"relay error")
            except Exception:
                pass
        finally:
            _RELAY_HITS[0] -= 1


_RELAY_BUILD = threading.Lock()


def _ensure_relay():
    """起本地中继。

    这里并发安全：起服务的过程放在单独的锁里，别的线程会等它起完，
    不会出现「一个线程正在起、另一个线程拿到空端口」这种竞态
    （上一版就是这毛病：预热线程还没起完，点播那边已经拿到空端口了）。
    """
    global _RELAY, _RELAY_TRIED
    with _RELAY_LOCK:
        if _RELAY is not None:
            return _RELAY
    with _RELAY_BUILD:
        with _RELAY_LOCK:
            if _RELAY is not None:
                return _RELAY
            if _RELAY_TRIED:
                return (None, 0)
        srv = None
        for _ in range(3):
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
                break
            except Exception:
                srv = None
                time.sleep(0.15)
        if srv is None:
            with _RELAY_LOCK:
                _RELAY_TRIED = True
                _RELAY = (None, 0)
            return (None, 0)
        srv.daemon_threads = True
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.3})
        t.daemon = True
        t.start()
        port = int(srv.server_address[1])
        with _RELAY_LOCK:
            _RELAY = (srv, port)
        return _RELAY


def _relay_prefix():
    srv, port = _ensure_relay()
    if not port:
        return ""
    return "http://127.0.0.1:%d/xsz?u=" % port


def _relay_root():
    """中继服务根地址（写播放列表里的分片用，不能带 /xsz?u=）"""
    srv, port = _ensure_relay()
    if not port:
        return ""
    return "http://127.0.0.1:%d" % port


def _relay_wrap(u):
    p = _relay_prefix()
    return (p + quote(u, safe="")) if (p and u) else ""


def _relay_unwrap(u):
    """纯语法剥离中继前缀，不触发任何网络动作。"""
    if not u:
        return u
    for p in ("/xsz?u=", "/proxy?url="):
        if p in u and u.startswith("http://127.0.0.1:"):
            return unquote(u.split(p, 1)[1])
    return u


def _relay_selftest(target, timeout=10):
    """插件内部走一遍中继：播放列表 → 前 1KB 分片，真读到媒体字节才算通。"""
    p = _relay_prefix()
    if not p:
        return False, "中继端口没能启动"
    u = p + quote(target, safe="")
    st, ct, body = _urllib_get(u, {"User-Agent": _UA_ANDROID}, timeout)
    if st != 200 or not body or body[:7] != b"#EXTM3U":
        return False, "中继取播放列表失败(HTTP %s)" % st
    seg = ""
    for line in body.decode("utf-8", "ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            seg = s
            break
    if not seg:
        return False, "播放列表里没有分片"
    if seg.startswith("http://") and not seg.startswith("http://127.0.0.1:"):
        seg = _relay_wrap(seg)
    st2, ct2, b2 = _urllib_get(seg, {"User-Agent": _UA_ANDROID, "Range": "bytes=0-1023"}, timeout)
    if st2 not in (200, 206) or not b2:
        return False, "中继取分片失败(HTTP %s)" % st2
    if not (b2[:1] == b"\x47" or b2[4:8] == b"ftyp" or b2[:4] == b"\x00\x00\x00\x01"):
        return False, "分片不是有效媒体流(首字节 %r)" % b2[:4]
    return True, "中继自检通过"


def _selftest_cached(target, timeout=10, ttl=600):
    now = time.time()
    with _RELAY_LOCK:
        hit = _SELFTEST.get(target)
    if hit and now - hit[0] < ttl:
        return hit[1], hit[2]
    ok, why = _relay_selftest(target, timeout)
    with _RELAY_LOCK:
        _SELFTEST[target] = (now, ok, why)
    return ok, why


def _play_endpoint(target):
    """决定最终交给播放器的地址 → (地址, 模式, 给人看的说明)。"""
    if not target:
        return "", "none", "没有解析到播放地址"
    mode = (_PLAY_MODE[0] or "auto").lower()

    # 配了服务器中转就优先用它：任何播放器都能直接播，不依赖 127.0.0.1
    up = _MEDIA_UPSTREAM[0]
    if up and mode != "direct":
        u = (up + quote(target, safe="")) if (up.endswith("?u=") or up.endswith("=")) \
            else (up.rstrip("/") + "/" + quote(target, safe=""))
        return u, "server", "服务器中转"

    if mode == "direct":
        return target, "direct", "直连（播放器需自带 Referer/Origin）"
    p = _relay_prefix()
    if not p:
        return target, "direct", "中继端口起不来，已退回直连"
    ok, why = _selftest_cached(target)
    if ok:
        return p + quote(target, safe=""), "relay", "本地中继 · 自检通过"
    if mode == "relay":
        return p + quote(target, safe=""), "relay", "本地中继 · 自检未过：" + why
    return target, "direct", "中继自检未过（%s），已退回直连" % why


def _play_desc_quick(target):
    """详情页用：只报「打算怎么播」，不联网，不拖慢打开速度。"""
    if not target:
        return "没有解析到播放地址"
    if _MEDIA_UPSTREAM[0]:
        return "服务器中转"
    if (_PLAY_MODE[0] or "auto").lower() == "direct":
        return "直连（播放器需自带 Referer/Origin）"
    if not _relay_root():
        return "中继端口起不来，已退回直连"
    return "本地中继（点播时自检）"


def _player_headers():
    """交给播放器的头：UA 必须给，实测 CDN 对空 UA 直接 403。"""
    h = _headers(referer=_site_home(), want_origin=True, accept="*/*")
    h["User-Agent"] = _UA_ANDROID
    return h


# ══════════════════════════════════════════════════════════════════════════
# 卷七  集地址归一化
# ══════════════════════════════════════════════════════════════════════════

def _norm_ids(ids):
    """detailContent 入参四态归一化：str / list / tuple / 逗号串。"""
    if isinstance(ids, (list, tuple)):
        parts = [str(x) for x in ids]
    else:
        parts = str(ids or "").split(",")
    out = []
    for p in parts:
        p = _relay_unwrap(p.strip())
        p = p.split("#")[0].split("?")[0]
        if "/" in p:
            p = p.rstrip("/").rsplit("/", 1)[-1]
        p = p.strip()
        if p:
            out.append(p)
    return out


def _parse_ep_token(tok):
    """集 token → (vid, 清晰度|'auto')，兼容壳的各种改写形态。"""
    s = str(tok or "")
    if "$" in s:
        s = s.split("$", 1)[1]
    s = _relay_unwrap(s.strip())
    try:
        s = unquote(s)
    except Exception:
        pass
    s = s.strip()

    parts = re.split(r"[|~]", s)
    head = re.split(r"@@", parts[0])[0].strip()
    if "/" in head:
        head = head.rstrip("/").rsplit("/", 1)[-1]
    head = head.split("?")[0].split("#")[0].strip()
    vid = head

    # 壳回传完整 CDN 地址时（/v4/<ID>_1080p/v.m3u8），反解出 ID 与清晰度
    um = re.search(r"/(\d+)_(\d{3,4})p/", s)
    if um:
        vid = um.group(1)
        if len(parts) <= 1:
            return vid, int(um.group(2))

    if len(parts) > 1:
        h = re.split(r"@@", parts[1].strip())[0].lower()
        if "auto" in h:
            return vid, "auto"
        m = re.search(r"(\d{3,4})", h)
        if m:
            return vid, int(m.group(1))
    return vid, "auto"


# ══════════════════════════════════════════════════════════════════════════
# 卷八  Spider
# ══════════════════════════════════════════════════════════════════════════

class Spider:

    def __init__(self):
        self.site = _site()
        try:
            t = threading.Thread(target=self._warmup)
            t.daemon = True
            t.start()
        except Exception:
            pass

    def _warmup(self):
        try:
            _load_ticket()
        except Exception:
            pass
        try:
            _open_session()
        except Exception:
            pass
        try:
            _ensure_relay()
        except Exception:
            pass

    # ── 契约方法 ────────────────────────────────────────────────────────

    def getDependence(self):
        return []

    def init(self, extend=""):
        """extend 三种写法：

        1) 纯地址:  "https://cn.xsz-av.com"
        2) JSON:    {"host": "https://cn.xsz-av.com"}
        3) 带过盾配置（推荐，被盾拦时的两条活路）:
           {
             "host":   "https://cn.xsz-av.com",
             "relay":  "https://你的域名/unlock?u=",   # 自己的过盾中转
             "media":  "http://你的IP:8787/u?k=口令&u=",   # 播放中转（黑屏就配这个，最稳）
             "play":   "auto",                        # auto / relay / direct
             "ticket": "手机浏览器里拿到的 cf_clearance 值",
             "ua":     "拿票时那台手机浏览器的 UA"
           }
        """
        global _SITE
        cfg = extend
        if isinstance(cfg, str) and cfg.strip().startswith("{"):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = extend
        if isinstance(cfg, dict):
            h = cfg.get("host") or cfg.get("url") or cfg.get("site")
            if h and str(h).startswith("http"):
                _SITE = str(h).rstrip("/")
            up = cfg.get("relay") or cfg.get("proxy") or cfg.get("unlock")
            if up:
                _RELAY_UPSTREAM[0] = str(up).strip()
            mv = cfg.get("media") or cfg.get("mrelay") or cfg.get("playrelay")
            if mv:
                _MEDIA_UPSTREAM[0] = str(mv).strip()
            pm = cfg.get("play") or cfg.get("playmode") or cfg.get("mode")
            if pm:
                _PLAY_MODE[0] = str(pm).strip().lower()
            tk = cfg.get("ticket") or cfg.get("cf_clearance")
            if tk:
                _set_ticket(str(tk), str(cfg.get("ua") or ""))
        elif isinstance(cfg, str):
            s = cfg.strip()
            if s.startswith("http"):
                _SITE = s.rstrip("/")
        self.site = _site()
        _load_ticket()
        return self.site

    def homeContent(self, filter=None):
        return {
            "class": [{"type_id": tid, "type_name": name} for name, tid in _CLASSES],
            "filters": {},
        }

    def homeVideoContent(self):
        try:
            html = _get_text_retry(_list_url("japanese", 1), referer=_site_home())
            return {"list": _maybe_hinted(_parse_list(html))}
        except Exception:
            return {"list": _maybe_hinted([])}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg) if str(pg).isdigit() else 1
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        tid = str(tid or "japanese").strip().lstrip("/")
        if tid not in _CLASS_MAP.values():
            tid = "japanese"
        try:
            html = _get_text_retry(_list_url(tid, pg), referer=_site_home())
            items = _maybe_hinted(_parse_list(html))
            return {
                "list": items,
                "page": pg,
                "pagecount": _parse_pagecount(html, pg),
                "limit": len(items),
                "total": 0,
            }
        except Exception:
            return {"list": _maybe_hinted([]), "page": pg, "pagecount": pg, "limit": 0, "total": 0}

    def detailContent(self, ids):
        out = []
        try:
            _vids = _norm_ids(ids)
            if _vids and str(_vids[0]) == _BLOCKED_ID:
                return {"list": [_blocked_detail()]}
        except Exception:
            pass
        try:
            for vid in _norm_ids(ids)[:1]:
                html = _get_text_retry(_video_url(vid), referer=_site_home(),
                                       timeout=_DETAIL_TIMEOUT)
                info = _parse_detail(html, vid)
                master, variants = _resolve_play(vid, html=html)

                eps = []
                if master:
                    eps.append("默认$%s|auto" % vid)
                    hs = sorted([h for h in variants.keys()
                                 if isinstance(h, int) and h > 0], reverse=True)
                    for h in hs:
                        eps.append("%dP$%s|%d" % (h, vid, h))
                if not eps:
                    eps.append("播放$%s|auto" % vid)

                try:
                    _hv = sorted([h for h in variants.keys()
                                  if isinstance(h, int) and h > 0], reverse=True)
                    _tu = (variants.get(1080) or variants.get(720)
                           or (variants[_hv[0]] if _hv else master))
                    _d = _play_desc_quick(_tu)
                    if _d:
                        info["vod_content"] = ((info.get("vod_content") or "").rstrip()
                                               + "\n\n[播放] " + _d).strip()
                except Exception:
                    pass

                info["vod_play_from"] = "XSZAV"
                info["vod_play_url"] = "#".join(eps)
                out.append(info)
        except Exception:
            return {"list": []}
        return {"list": out}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = int(pg) if str(pg).isdigit() else 1
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        kw = str(key or "").strip()
        if not kw:
            return {"list": [], "page": pg, "pagecount": pg}
        try:
            html = _get_text_retry(_search_url(kw, pg), referer=_site_home())
            items = _maybe_hinted(_parse_list(html))
            return {
                "list": items,
                "page": pg,
                "pagecount": _parse_pagecount(html, pg),
                "limit": len(items),
                "total": 0,
            }
        except Exception:
            return {"list": _maybe_hinted([]), "page": pg, "pagecount": pg, "limit": 0, "total": 0}

    def playerContent(self, flag, ids, vipFlags=None):
        """返回播放地址。

        策略（extend 里 "play" 可强制 relay / direct）：
          中继自检通过 -> 交 127.0.0.1 中继地址（Referer/Origin/UA 由插件补齐）
          自检不过     -> 退回 CDN 直连 + header（播放器自己能带 ref/origin 时可用）
        """
        header = _player_headers()
        empty = {"parse": 0, "jx": 0, "playUrl": "", "url": "",
                 "format": "application/x-mpegURL", "header": header, "headers": header}
        try:
            vid, want = _parse_ep_token(ids)
            if (not vid or not vid.isdigit()) and isinstance(vipFlags, str) and vipFlags.strip():
                vid, want = _parse_ep_token(vipFlags)
            if not vid:
                return empty

            target, _heights = _pick_target(vid, want)
            if not target:
                _PLAY_NOTE[0] = "详情页里没解析到 m3u8"
                return empty

            url, mode, desc = _play_endpoint(target)
            if not url:
                return empty
            _PLAY_NOTE[0] = desc
            return {"parse": 0, "jx": 0, "playUrl": "", "url": url,
                    "format": "application/x-mpegURL", "header": header, "headers": header}
        except Exception:
            return empty

    def localProxy(self, param):
        """壳把 /proxy?url=... 交给这里：返回 [状态, MIME, bytes, dict]。"""
        try:
            if isinstance(param, str):
                try:
                    param = json.loads(param)
                except Exception:
                    param = {"url": param}
            if not isinstance(param, dict):
                param = {}
            target = param.get("url") or param.get("u") or ""
            if isinstance(target, list):
                target = target[0] if target else ""
            target = unquote(str(target or "").strip())
            if not target.startswith("http"):
                return [404, "text/plain", b"bad url", {}]
            st, ct, body, _hx = _relay_fetch(target)
            if not isinstance(body, bytes):
                body = str(body).encode("utf-8", "ignore")
            return [int(st), ct or "application/octet-stream", body, {}]
        except Exception:
            return [502, "text/plain", b"proxy error", {}]

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        try:
            u = str(url or "").lower()
            return (".m3u8" in u) or (".mp4" in u) or ("mpegurl" in u) or ("/xsz?u=" in u) or ("/s/" in u)
        except Exception:
            return False

    def action(self, action):
        return {}

    def destroy(self):
        try:
            srv, port = _RELAY if _RELAY else (None, 0)
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════════════════════════
# 本地自测入口（不影响 TVBox 加载）
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sp = Spider()
    sp.init("")
    print("(0) 通道体检 —— 谁能过盾：")
    for _n, _r in _probe_channels():
        print("      %-13s %s" % (_n, _r))
    print("(1) 中继   :", _relay_prefix() or "(不可用)")

    hc = sp.homeContent()
    print("(2) 分类   :", len(hc["class"]), [c["type_name"] for c in hc["class"]])

    cc = sp.categoryContent("japanese", 1, False, None)
    print("(3) 列表   :", len(cc["list"]), "pagecount =", cc["pagecount"])
    if cc["list"]:
        v = cc["list"][0]
        print("    首条   :", v["vod_id"], "|", v["vod_name"][:32], "|", v["vod_remarks"])

    vz = sp.categoryContent("hanime", 2, False, None)
    print("(4) 分页   :", len(vz["list"]), [x["vod_id"] for x in vz["list"][:3]])

    sc = sp.searchContent("ERDM-168", False, "1")
    print("(5) 搜索   :", len(sc["list"]), [x["vod_id"] for x in sc["list"][:3]])

    if cc["list"]:
        dc = sp.detailContent([cc["list"][0]["vod_id"]])
        if dc["list"]:
            d = dc["list"][0]
            print("(6) 详情   :", d["vod_name"][:34], "|", d["vod_year"], "|", d["vod_remarks"])
            print("    选集   :", d["vod_play_url"][:110])
            tok = d["vod_play_url"].split("#")[0].split("$", 1)[1]
            pc = sp.playerContent("XSZAV", tok, None)
            print("(7) 播放   :", (pc["url"] or "(EMPTY)")[:100])
            st, ct, body = (0, "", b"")
            if pc["url"]:
                with urllib.request.urlopen(pc["url"], timeout=25) as r:
                    st, ct, body = r.status, r.headers.get("Content-Type"), r.read()
            print("    母带   :", st, ct, len(body), body[:16])
            if body[:7] == b"#EXTM3U":
                first = [l for l in body.decode("utf-8", "ignore").splitlines()
                         if l and not l.startswith("#")]
                if first:
                    with urllib.request.urlopen(first[0], timeout=25) as r:
                        seg = r.read()
                    print("    分片   :", r.status, r.headers.get("Content-Type"),
                          len(seg), seg[:4])

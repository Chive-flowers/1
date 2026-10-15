# -*- coding: utf-8 -*-
"""juku 短剧聚合 —— TVBox py 源插件（内置服务地址版 v2）

把 juku 后台服务的 /web/api 接口映射成 TVBox 标准源接口。
支持: 推荐流 / 随机 / 聚合分类 / 单平台浏览 / 搜索 / 详情选集 / 播放

依赖: 只需 Python 标准库

地址解析顺序（前面的探不通就自动往后退）：
  1) ext 里填的地址
  2) 内置地址 JUKU_URL（本文件顶部常量，默认 http://127.0.0.1:8998）
  3) 后台服务写出来的 service_url / juku.port / port.txt / 入口地址.txt
  4) 常见端口扫描（8998/8999/9000-9029 优先）
"""

import json
import re
import socket
import time

try:
    from urllib.request import Request, urlopen, build_opener, ProxyHandler
    from urllib.parse import quote, urlencode
    _PY3 = True
except ImportError:  # pragma: no cover
    from urllib import quote, urlencode
    from urllib2 import Request, urlopen, build_opener, ProxyHandler
    _PY3 = False

# 壳/系统里若配了代理，本机(127.0.0.1)请求可能被劫走 —— 这里显式禁用代理
_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        try:
            _OPENER = build_opener(ProxyHandler({}))
        except Exception:
            _OPENER = False
    return _OPENER or None


UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# ============================================================
# 内置服务地址（改这一行就能换地址；ext 留空也能直连）
#   8998 = juku-node 入口默认端口
# ============================================================
JUKU_URL = "http://127.0.0.1:8998"

# 探测超时（秒）：TCP 快筛必须极短（本地未监听端口是立即拒绝，只有不可达地址才会等）
PROBE_TCP_TIMEOUT = 0.25
PROBE_HTTP_TIMEOUT = 2.5
SCAN_HTTP_TIMEOUT = 1.5

# 后台服务会把「实际端口 / 完整地址」写到下面这些文件里。
# 路径按新包（juku-node-fixed）的落点排前面，旧路径保留兜底。
PORT_FILES = [
    "/storage/emulated/0/juku-node-fixed/service_url",
    "/storage/emulated/0/juku-node-fixed/juku.port",
    "/storage/emulated/0/juku-node-fixed/port.txt",
    "/storage/emulated/0/juku-node-fixed/data/juku.port",
    "/storage/emulated/0/juku-node-fixed/data/入口地址.txt",
    "/sdcard/juku-node-fixed/service_url",
    "/sdcard/juku-node-fixed/juku.port",
    "/sdcard/juku-node-fixed/port.txt",
    "/sdcard/juku-node-fixed/data/juku.port",
    "/sdcard/juku-node-fixed/data/入口地址.txt",
    "/storage/emulated/0/juku/juku.port",
    "/storage/emulated/0/juku/port.txt",
    "/storage/emulated/0/juku/port.txt",
    "/storage/emulated/0/juku-lite/port.txt",
    "/storage/emulated/0/port.txt",
    "/sdcard/juku/port.txt",
    "/sdcard/Juku/port.txt",
    "/sdcard/juku-lite/port.txt",
    "/sdcard/TVBox/juku/port.txt",
    "/sdcard/TVBox/port.txt",
    "/sdcard/port.txt",
    "/data/local/tmp/port.txt",
    "/data/local/tmp/juku/port.txt",
]

# 自动探测主机（只扫本机：TVBox 和后台跑在同一台手机上，扫不可达地址会白等）
PROBE_HOSTS = ["127.0.0.1", "localhost"]
PROBE_PORTS = [
    # juku-node 入口：默认 8998，被占会往后顺延（8999 → 9029）
    8998, 8999, 9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007,
    9008, 9009, 9010, 9011, 9012, 9013, 9014, 9015, 9016, 9017,
    9018, 9019, 9020, 9021, 9022, 9023, 9024, 9025, 9026, 9027,
    9028, 9029,
    # 其它常见段（旧包 / 别的服务）
    8090, 8080, 8081, 8000, 8001, 8008, 8088, 8888, 8899, 8686,
    9090, 9999, 12345, 12346, 7788, 7777, 6688, 6666, 5555,
    3000, 5000, 7000, 7100, 7200, 18888, 19999,
]


def _norm_base(u):
    """把一条文件内容 / 用户输入，规整成 http://host:port 形式（去掉 /web/ 之类的尾巴）。"""
    if not u:
        return ""
    u = str(u).strip()
    m = re.search(r"https?://[^\s\"'<>]+", u)
    if m:
        u = m.group(0)
    else:
        m2 = re.search(r"(\d{2,5})", u)
        if not m2:
            return ""
        p = int(m2.group(1))
        if p < 1024 or p > 65535:
            return ""
        return "http://127.0.0.1:%d" % p
    # 只取 scheme://host:port，砍掉后面的路径
    m3 = re.match(r"(https?://[^/\s]+)", u)
    if not m3:
        return ""
    return m3.group(1).rstrip("/")


TIMEOUT = 25
# 首页（聚合分类 / 平台列表）单独给一个更短的超时：
# 这两个只是拼分类栏，不能因为后台慢就让整个首页干等、把壳拖超时。
HOME_TIMEOUT = 8

VERSION = "2.6.0"
VERSION_SHORT = "2.6"

# 后台实时分类拉不到时的种子分类（聚合分类的 cat 参数就是这个名字）。
# 这些名字来自用户后台真实出现的聚合分类：改不动后台也不至于只剩「推荐/随机」两条。
SEED_UNIONS = ["推荐", "最新", "新剧", "最新剧场"]

# 分类缓存：后台答话时把「聚合分类 + 平台列表」存下来；
# 下次后台慢/没起的时候直接用缓存拼分类栏，不再只剩「推荐/随机」。
CATS_CACHE_FILES = [
    "/storage/emulated/0/juku-node-fixed/juku-py-cats.json",
    "/sdcard/juku-node-fixed/juku-py-cats.json",
    "/storage/emulated/0/juku-py-cats.json",
    "/sdcard/juku-py-cats.json",
]

# 诊断日志落点（能写哪个写哪个，都写不了就算了，不影响功能）
LOG_FILES = [
    "/storage/emulated/0/juku-node-fixed/juku-py.log",
    "/sdcard/juku-node-fixed/juku-py.log",
    "/storage/emulated/0/juku-py.log",
    "/sdcard/juku-py.log",
    "/data/local/tmp/juku-py.log",
]

# 探测失败后多少秒内不再全端口重扫（避免每次进首页都干等）
RESCAN_GAP = 30


# juku 的 /web/api/* 统一包了一层壳：{"code":200,"data":{...}}（它自带的前端
# 也是「先判 code 再取 data」）。这层不剥掉，items/list 就永远在壳里面，取不到。
_API_WRAP_KEYS = {"code", "data", "msg", "message", "success", "status", "error"}


def _unwrap(resp):
    """把 {code,data} 壳剥掉，返回里面的真数据。没有壳的原样返回。"""
    d = resp
    for _ in range(5):
        if not isinstance(d, dict) or "data" not in d:
            break
        keys = set(d.keys())
        if "code" in keys or keys <= _API_WRAP_KEYS:
            inner = d.get("data")
            if inner is None:
                break
            d = inner
            continue
        break
    return d


def _split_hostport(base):
    """从 http://host:port 里拆出 host / port，供 TCP 快筛用。"""
    m = re.match(r"(https?)://([^/:\s]+)(?::(\d+))?", str(base or ""))
    if not m:
        return "", 0
    scheme, host, port = m.group(1), m.group(2), m.group(3)
    if port:
        return host, int(port)
    return host, (443 if scheme == "https" else 80)


def _cats_cache_write(unions, plats):
    """把分类数据存一份到手机存储（能写哪个写哪个），下次后台不答话时兜底。"""
    if not unions and not plats:
        return ""
    blob = json.dumps({"ts": int(time.time()), "unions": unions, "plats": plats},
                      ensure_ascii=False)
    for f in CATS_CACHE_FILES:
        try:
            with open(f, "w", encoding="utf-8") as fp:
                fp.write(blob)
            return f
        except Exception:
            continue
    return ""


def _cats_cache_read():
    """读分类缓存，返回 (unions, plats, 说明)。没有就返回空。"""
    for f in CATS_CACHE_FILES:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.loads(fp.read())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        u = [x for x in (d.get("unions") or []) if isinstance(x, dict)]
        p = [x for x in (d.get("plats") or []) if isinstance(x, dict)]
        if u or p:
            try:
                when = time.strftime("%m-%d %H:%M", time.localtime(int(d.get("ts") or 0)))
            except Exception:
                when = "?"
            return u, p, "%s（存于 %s）" % (f, when)
    return [], [], ""


def _short(e, n=70):
    t = str(e)
    t = t.replace("\n", " ")
    return t[:n]


def _log(msg):
    line = "[%s] [py %s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), VERSION, msg)
    for p in LOG_FILES:
        try:
            fp = open(p, "a")
            try:
                fp.write(line)
            finally:
                fp.close()
            return p
        except Exception:
            continue
    return ""


class Spider(object):

    def __init__(self):
        self.ext = ""
        self.base = ""
        self.teen = False
        self._plats = None
        self._unions = None
        self._last_err = ""
        self._trace = []
        self._base_src = ""
        self._fail_ts = 0.0
        self._last_raw = {}
        self._last_body = ""
        self._port_alive = []
        self._api_ok = False   # True = 真的收到了后台接口回包；False = 只是端口开着 
        self._no_status = False
        self._cats_src = ""      # 分类数据来源：实时 / 缓存
        self._cats_note = ""     # 缓存文件位置与时间
        self._card_text = {}     # 各类“说明卡”点开后的正文（按 vid 存）

    # ------------------------------------------------------------------ init

    def init(self, extend=""):
        self.ext = (extend or "").strip()
        if self.ext.startswith("{"):
            try:
                _d = json.loads(self.ext)
                if isinstance(_d, dict) and str(_d.get("status", "1")).lower() in ("0", "false", "no", "off"):
                    self._no_status = True
            except Exception:
                pass
        self.base = self._resolve_base(self.ext)
        _log("init 完成 base=%s 来源=%s" % (self.base or "(空)", self._base_src or "-"))
        return self

    def _resolve_base(self, ext):
        # 节流：刚扫失败过就别再全扫一遍，直接返回空（省得进首页干等）
        now = time.time()
        if self._fail_ts and (now - self._fail_ts) < RESCAN_GAP:
            if not self._trace:
                self._trace.append("距上次探测失败不足 %ds，本轮跳过重扫" % RESCAN_GAP)
            return ""
        self._trace = []
        # 1) ext 里直接给的地址优先
        ext_cands = [c.rstrip("/") for c in self._bases_from_ext(ext)]
        for cand in ext_cands:
            if self._probe(cand):
                self._base_src = "ext 里填的地址"
                self._api_ok = True
                return cand
        hit = self._alive_hit(ext_cands)
        if hit:
            self._base_src = "ext 地址端口活着、接口没答话：%s" % hit
            self._trace.append("ext 地址端口活着但接口没答话，先用它：%s" % hit)
            return hit
        # 2) 内置地址（顶部 JUKU_URL）：ext 留空也能直连
        b = _norm_base(JUKU_URL)
        if b and self._probe(b, timeout=PROBE_HTTP_TIMEOUT):
            self._base_src = "内置地址 %s" % b
            self._api_ok = True
            return b
        hit = self._alive_hit([b] if b else [])
        if hit:
            self._base_src = "内置地址端口活着、接口没答话：%s" % hit
            self._trace.append("内置地址端口活着但接口没答话，先用它：%s" % hit)
            return hit
        # 3) 后台服务写出来的地址 / 端口文件
        file_cands = [c.rstrip("/") for c in self._bases_from_files()]
        for c in file_cands:
            if self._probe(c, timeout=PROBE_HTTP_TIMEOUT):
                self._base_src = "地址文件"
                self._api_ok = True
                return c
        hit = self._alive_hit(file_cands)
        if hit:
            self._base_src = "地址文件端口活着、接口没答话：%s" % hit
            self._trace.append("地址文件端口活着但接口没答话，先用它：%s" % hit)
            return hit
        # 4) 常见端口扫描（这一段不逐条记流水，只报汇总）
        n = 0
        for host in PROBE_HOSTS:
            for port in PROBE_PORTS:
                c = "http://%s:%d" % (host, port)
                n += 1
                if self._probe(c, timeout=SCAN_HTTP_TIMEOUT, note=False, fallback=False):
                    self._base_src = "扫端口命中 %s" % c
                    self._api_ok = True
                    return c
        self._trace.append("扫端口：%d 个候选（%s … %s）全部没通"
                           % (n, PROBE_PORTS[0], PROBE_PORTS[-1]))
        if self._port_alive:
            def _rank(u):
                _, p = _split_hostport(u)
                if 8998 <= p <= 9029:
                    return (0, p)
                if p in (8090, 8080, 5555, 18888):
                    return (1, p)
                return (2, p)
            b = sorted(self._port_alive, key=_rank)[0]
            self._base_src = "端口活着但接口没答话：%s" % b
            self._trace.append("改用「端口活着」的地址兜底：%s" % b)
            _log("探测：接口没答话，用活端口兜底 %s" % b)
            return b
        self._fail_ts = now
        self._trace.append("所有候选都试完，没找到后台")
        _log("探测失败：%s" % " | ".join(self._trace[-10:]))
        return ""

    def _alive_hit(self, cands):
        """在这些明确地址里找「端口活着但接口没答话」的，命中就返回它。
        （明确地址 = 用户填的 / 内置的 / 后台自己写的；扫端口扫到的不算）"""
        for c in (cands or []):
            c = (c or "").rstrip("/")
            if c and c in self._port_alive:
                return c
        return ""

    def _bases_from_ext(self, ext):
        out = []
        if not ext:
            return out
        ext = ext.strip()
        if ext.startswith("{"):
            try:
                d = json.loads(ext)
                if isinstance(d, dict):
                    if d.get("teen"):
                        self.teen = True
                    for k in ("base", "url", "host", "api", "server"):
                        v = d.get(k)
                        if isinstance(v, str) and v.startswith("http"):
                            out.append(v)
            except Exception:
                pass
        for m in re.finditer(r"https?://[^\s,;\"'|]+", ext):
            out.append(m.group(0))
        # 去重（JSON 里的 base 和裸链接常常是同一个地址，重复探测纯浪费时间）
        seen = set()
        uniq = []
        for u in out:
            u = (u or "").rstrip("/")
            if u and u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    def _bases_from_files(self):
        """读后台写的地址文件：整条 URL 优先，纯数字当端口用。去重后按顺序返回。"""
        out = []
        seen = {}
        found = 0
        for p in PORT_FILES:
            try:
                with open(p, "r") as fp:
                    txt = fp.read().strip()
            except Exception:
                continue
            if not txt:
                self._trace.append("文件存在但是空的：%s" % p)
                continue
            found += 1
            self._trace.append("读到 %s = %s" % (p.split("/")[-1], txt[:40]))
            c = _norm_base(txt)
            if not c:
                continue
            # 只认第一行有效地址，避免文件里带的说明行干扰
            if c in seen:
                continue
            seen[c] = 1
            out.append(c)
        if not found:
            self._trace.append("一个地址文件都没读到（路径不对或壳里没存储权限）")
        return out

    def _probe(self, base, timeout=PROBE_HTTP_TIMEOUT, note=True, fallback=True):
        """判活三步：TCP 连端口（本地毫秒级）→ 拉 /web/api/platforms → 看回包像不像 juku。
        空数据也算通（平台还没采集出来是老问题，不能当没起）。
        note=False 时不逐条记流水（扫端口阶段用，免得把关键信息淹掉）。"""
        base = (base or "").rstrip("/")
        if not base:
            return False
        host, port = _split_hostport(base)
        if not port:
            return False
        try:
            sk = socket.create_connection((host, port), PROBE_TCP_TIMEOUT)
            sk.close()
        except Exception as e:
            if note:
                self._trace.append("%s → 端口不通（%s）" % (base, _short(e, 40)))
            return False
        url = base + "/web/api/platforms"
        try:
            data = self._raw(url, timeout)
        except Exception as e:
            code = getattr(e, "code", None)
            try:
                code = int(code) if code is not None else None
            except Exception:
                code = None
            if code is not None and 400 <= code < 500:
                # 这个端口上确实有服务，但明确没有 juku 的接口 —— 判死，不当兜底
                if note:
                    self._trace.append("%s → 有服务但不是 juku（接口回 %s）" % (base, code))
                return False
            # 超时 / 连接中断：端口活着却没答话，留作兜底候选
            if note:
                self._trace.append("%s → 端口活着，接口没答话（%s）" % (base, _short(e, 60)))
            if fallback and base not in self._port_alive:
                self._port_alive.append(base)
            return False
        if isinstance(data, dict):
            keys = set(data.keys())
            if "code" in keys or (keys & set(["platforms", "unions"])):
                if note:
                    self._trace.append("%s → 通了（回包字段：%s）" % (base, ",".join(list(keys)[:5])))
                return True
            if note:
                self._trace.append("%s → 有服务但不是 juku（回包字段：%s）"
                                   % (base, ",".join(list(keys)[:5])))
            return False
        if isinstance(data, list):
            if note:
                self._trace.append("%s → 通了（list %d 条）" % (base, len(data)))
            return True
        if note:
            self._trace.append("%s → 返回的不是 JSON" % base)
        return False

    # ------------------------------------------------------------------ http

    def _raw(self, url, timeout=TIMEOUT):
        req = Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": self.base + "/" if self.base else url,
        })
        op = _opener()
        resp = op.open(req, timeout=timeout) if op else urlopen(req, timeout=timeout)
        body = resp.read()
        try:
            txt = body.decode("utf-8")
        except Exception:
            txt = body.decode("utf-8", "ignore")
        self._last_body = txt[:600]
        return json.loads(txt)

    def _get(self, path, **params):
        # retry=1：只有在「很快就失败」时才重试一次（连接被拒 / 秒断 / 回包坏），
        # 如果是整个超时了（后台真的慢），就不重试，免得把壳拖到超时白屏。
        retry = int(params.pop("retry", 0) or 0)
        tmo = params.pop("tmo", None) or TIMEOUT
        if not self.base:
            self.base = self._resolve_base(self.ext)
        if not self.base:
            self._last_err = "后台服务未启动或地址未填"
            return {}
        if self.teen and "teen=" not in path:
            params.setdefault("teen", "1")
        if params:
            qs = urlencode(dict((k, v) for k, v in params.items() if v != ""))
            if qs:
                path = path + ("&" if "?" in path else "?") + qs
        url = self.base + path
        raw = None
        for attempt in range(retry + 1):
            t0 = time.time()
            try:
                raw = self._raw(url, tmo)
                break
            except Exception as e:
                cost = time.time() - t0
                self._last_err = "%s → %s" % (path.split("?")[0], _short(e, 90))
                _log("GET fail(%d) %s: %s" % (attempt + 1, url, e))
                if attempt < retry and cost < 6:
                    time.sleep(0.35)
                    continue
                return {}
        if not isinstance(raw, dict):
            self._last_raw = {"__list__": raw} if isinstance(raw, list) else {}
            d = _unwrap(raw)
            return {"list": d} if isinstance(d, list) else {}
        self._last_raw = raw
        code = raw.get("code")
        if code is not None:
            try:
                if int(code) != 200:
                    msg = raw.get("msg") or raw.get("message") or ""
                    self._last_err = "%s → code=%s %s" % (path.split("?")[0], code, msg)
                    _log("接口返回非 200：%s" % self._last_err)
                    if retry:
                        time.sleep(0.35)
                        return self._get(path, tmo=tmo, **dict(params, retry=retry - 1))
                    return {}
            except Exception:
                pass
        d = _unwrap(raw)
        if isinstance(d, list):
            return {"list": d}
        return d if isinstance(d, dict) else {}

    def _abs(self, u):
        if not u:
            return ""
        u = str(u).strip()
        if u.startswith("//"):
            return ("http:" + u) if self.base.startswith("http:") else ("https:" + u)
        if u.startswith("http://") or u.startswith("https://"):
            return u
        if u.startswith("/"):
            return self.base.rstrip("/") + u
        return u

    # ------------------------------------------------------------- 数据规整

    @staticmethod
    def _pick(d, *keys):
        for k in keys:
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            return v
        return ""

    def _plat_of(self, it, fallback=""):
        p = self._pick(it, "plat", "platform", "platKey", "source", "site")
        if isinstance(p, dict):
            p = self._pick(p, "name", "key", "plat", "id")
        return str(p or fallback or "").strip()

    def _card(self, it, plat_fallback=""):
        if not isinstance(it, dict):
            return None
        # ⚠ 真机 /feed 用的是 drama_id / drama_name，/list 等用的是 id / name —— 两套都得认
        vid = self._pick(it, "id", "vod_id", "drama_id", "dramaId", "series_id", "seriesId",
                         "shortPlayId", "did", "key")
        name = self._pick(it, "name", "drama_name", "dramaName", "title", "vod_name",
                          "series_name", "seriesName", "shortPlayName", "short_play_name")
        if vid == "" and isinstance(it.get("id"), (int, float)):
            vid = str(it.get("id"))
        if vid == "" or not name:
            return None
        plat = self._plat_of(it, plat_fallback)
        pic = self._pick(it, "cover", "pic", "coverUrl", "cover_url", "image", "poster",
                         "verticalImg", "horizonPoster", "thumbnailUrl", "vod_pic")
        remark = self._pick(it, "remarks", "remark", "badge", "note", "serial_status",
                            "updateStatus", "status", "score_str", "play_count_text")
        if not remark:
            total = self._pick(it, "total", "episode_count", "episodeCount", "episodes_num",
                               "totalEpisodes", "total_episode_num")
            epno = self._pick(it, "episode_no", "episodeNo")
            if total:
                remark = "共%s集" % total
            elif epno:
                remark = "第%s集" % epno
        return {
            "vod_id": "%s|%s" % (plat, vid),
            "vod_name": str(name),
            "vod_pic": self._abs(pic),
            "vod_remarks": str(remark or ""),
        }

    def _cards(self, seq, plat_fallback=""):
        out = []
        seen = set()
        for it in (seq or []):
            if not isinstance(it, dict):
                continue
            c = self._card(it, plat_fallback)
            if not c or c["vod_id"] in seen:
                continue
            seen.add(c["vod_id"])
            out.append(c)
        return out

    # --------------------------------------------------------- 分类栏数据源
    #
    # 分类栏 = 聚合分类(/unions) + 平台列表(/platforms)，两个接口各自独立。
    # 老版是串行拉：unions 慢 8 秒 + platforms 慢 8 秒 = 16 秒，壳早超时了 →
    # 分类栏一条都拼不出来，只剩「推荐/随机」。现在并行拉 + 总预算封顶。
    def _fetch_cats(self, force=False):
        if not force and (self._unions or self._plats):
            return self._unions or [], self._plats or []
        # 先把地址定下来再并发：两个线程同时触发探测会白扫一遍
        if not self.base:
            self.base = self._resolve_base(self.ext)

        budget = HOME_TIMEOUT
        got = {}

        def _one(key, fn):
            try:
                got[key] = fn()
            except Exception as e:  # pragma: no cover
                _log("拉 %s 出错：%s" % (key, e))
                got[key] = []

        jobs = [("unions", self._get_unions), ("plats", self._get_plats)]
        try:
            import threading
            ths = []
            for k, fn in jobs:
                th = threading.Thread(target=_one, args=(k, fn))
                th.setDaemon(True)
                th.start()
                ths.append(th)
            deadline = time.time() + budget
            for th in ths:
                left = deadline - time.time()
                if left > 0:
                    th.join(left)
        except Exception:
            # 没线程就退化成串行，但每个都只给一半预算
            for k, fn in jobs:
                _one(k, fn)

        unions = [u for u in (got.get("unions") or []) if isinstance(u, dict)]
        plats = [p for p in (got.get("plats") or []) if isinstance(p, dict)]

        if unions or plats:
            self._unions, self._plats = unions, plats
            f = _cats_cache_write(unions, plats)
            self._cats_src = "实时"
            self._cats_note = ("已存 %s" % f) if f else "缓存没写进去（存储没权限）"
            return unions, plats

        # 实时拉不到 → 用上次存下来的
        cu, cp, note = _cats_cache_read()
        if cu or cp:
            self._unions, self._plats = cu, cp
            self._cats_src = "缓存"
            self._cats_note = note
            _log("后台没给分类，改用缓存：%s" % note)
            return cu, cp

        # 缓存也没有 → 用内置种子分类（cat 参数就是分类名，照样能查到东西）
        self._unions = [{"name": n, "plats": [], "_seed": True} for n in SEED_UNIONS]
        self._plats = []
        self._cats_src = "种子"
        self._cats_note = "后台一直没答话，分类栏用的是内置种子；点右侧「🔄 重连分类」可以重试"
        _log("后台没给分类、也没缓存，用内置种子：%s" % ",".join(SEED_UNIONS))
        return self._unions, self._plats

    # ------------------------------------------------------------- home 接口

    def homeContent(self, filter=None):
        classes = []
        filters = {}

        # 版本号直接挂在第一栏：py 换了没生效，看一眼这栏就知道（壳还在用旧 py / 旧缓存）
        classes.append({"type_id": "feed", "type_name": "🔥 推荐 v%s" % VERSION_SHORT})
        classes.append({"type_id": "mix", "type_name": "🎲 随机"})
        classes.append({"type_id": "short", "type_name": "📱 短剧流"})

        # 聚合分类 + 平台：并行拉一次；拉不到用缓存；再没有就用内置种子（分类栏不再只剩两条）
        unions, plats = self._fetch_cats()

        # 聚合分类（真机 /unions 项形如 {name, n, plats}，cat 参数就是 name）
        for u in unions:
            name = u.get("name") or u.get("title") or u.get("cat") or ""
            cat = name or u.get("cat") or u.get("key") or u.get("id") or ""
            if not name:
                continue
            n = u.get("n") or 0
            try:
                n = int(n or 0)
            except Exception:
                n = 0
            label = str(name) + ((" ·%d 站" % n) if n > 1 else "")
            classes.append({"type_id": "cat:%s" % cat, "type_name": label})

        # 各平台
        for p in plats:
            name = p.get("name") or p.get("key") or ""
            key = p.get("key") or p.get("id") or name
            if not name:
                continue
            tid = "plat:%s" % key
            classes.append({"type_id": tid, "type_name": str(name)})
            cats = self._cats_of(p)
            if cats:
                # ⚠ 真机平台分类是 {id, name}，/list 的 cat 参数要传 id（之前传成了名字 → 后台查不到 → 一片空）
                vals = [{"n": "全部", "v": ""}]
                for c in cats:
                    vals.append({"n": str(c.get("name") or c.get("key") or c.get("cat") or ""),
                                 "v": str(c.get("id") or c.get("key") or c.get("cat") or c.get("name") or "")})
                filters[tid] = [{"key": "cat", "name": "分类", "value": vals}]

        # 兜底活路：在「推荐 / 随机 / 短剧流」栏里也塞一个「聚合分类」筛选项。
        # 万一分类栏只出来一两条，从这里照样能进聚合分类。
        if unions:
            uvals = [{"n": "全部", "v": ""}]
            for u in unions:
                nm = str(u.get("name") or u.get("title") or u.get("cat") or "")
                if nm:
                    uvals.append({"n": nm, "v": nm})
            if len(uvals) > 1:
                for t in ("feed", "mix", "short"):
                    filters[t] = [{"key": "cat", "name": "聚合分类", "value": uvals}]

        if not self._no_status:
            st = self._conn_state()
            if st == "down":
                diag_name = "⚠ 后台未连接"
            elif st == "noanswer":
                diag_name = "⚠ 后台没应答 · 点我看原因"
            else:
                diag_name = "🔧 诊断 v%s" % VERSION_SHORT
                if self._cats_src == "缓存":
                    diag_name = "🔧 诊断 v%s · 分类来自缓存" % VERSION_SHORT
                elif self._cats_src == "种子":
                    diag_name = "🔧 诊断 v%s · 分类是内置种子" % VERSION_SHORT
            classes.append({"type_id": "diag", "type_name": diag_name})
        # 分类栏拼不出来时的一条活路：点它强制重拉一次，然后退出重进源
        classes.append({"type_id": "refresh", "type_name": "🔄 重连分类 v%s" % VERSION_SHORT})
        if not classes:
            classes = [{"type_id": "feed", "type_name": "🔥 推荐"}]
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        # 网页首页的「推荐」流就是 /mix?n=12（返回 list）；/feed 是竖屏短剧流（返回 items）。
        # 两个都试，谁先有货用谁。
        d = self._get("/web/api/mix", n=12, tmo=HOME_TIMEOUT, retry=1)
        items = self._pick(d, "list", "items") or []
        if not items:
            items = self._pick(self._get("/web/api/feed", n=12, blk=1, tmo=HOME_TIMEOUT),
                               "items", "list") or []
        lst = self._cards(items)
        card = self._status_card()
        if card:
            lst = [card] + lst
        if not lst:
            lst = self._empty_list()
        return {"list": lst}

    def _union_list(self, cat, pg=1):
        """聚合分类列表：/web/api/union?cat=<分类名>&page=<n>（cat 就是分类名）。"""
        d = self._get("/web/api/union", cat=cat, page=pg)
        items = self._pick(d, "list", "items") or []
        names = d.get("plat_names") or []
        lst = self._cards(items)
        for i, c in enumerate(lst):
            if i < len(names) and names[i]:
                c["vod_remarks"] = str(names[i])
        if not lst:
            lst = self._empty_list("「%s」暂时没数据" % cat,
                                   "接口问了但没回内容，可能这个分类后台还没采到；\n"
                                   "换个分类，或点「🔄 重连分类」重拉一次。")
        return {"list": lst, "page": pg, "pagecount": 9999 if len(lst) >= 12 else pg,
                "limit": 24, "total": len(lst)}

    # --------------------------------------------------------- category 接口

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = int(pg or 1)
        cat = ""
        if isinstance(extend, dict):
            cat = extend.get("cat") or extend.get("class") or ""
        if not cat and isinstance(filter, dict):
            cat = filter.get("cat") or ""

        if tid in ("diag", "refresh"):
            if tid == "refresh":
                self._unions = None       # 清掉内存里的旧结果，强制重拉
                self._plats = None
                u, p = self._fetch_cats(force=True)
                _log("手动重连分类：unions=%d plats=%d 来源=%s" % (len(u), len(p), self._cats_src))
            c = self._status_card()
            return {"list": ([c] if c else []), "page": 1, "pagecount": 1,
                    "limit": 24, "total": 1}

        if tid in ("feed", "home"):
            # 网页「推荐」页 = /mix?n=24；拿不到再退 /feed 竖屏流
            if cat:
                return self._union_list(cat, pg)
            items = self._pick(self._get("/web/api/mix", n=24, tmo=HOME_TIMEOUT, retry=1),
                               "list", "items") or []
            if not items:
                items = self._pick(self._get("/web/api/feed", n=24, blk=1), "items", "list") or []
            lst = self._cards(items)
            if not lst:
                lst = self._empty_list()
            return {"list": lst, "page": pg, "pagecount": 1, "limit": 24, "total": len(lst)}

        if tid == "short":
            # 竖屏短剧流：真机播放页刷的那条流，字段是 drama_id / drama_name
            items = self._pick(self._get("/web/api/feed", n=24, blk=1), "items", "list") or []
            lst = self._cards(items)
            if not lst:
                lst = self._empty_list()
            return {"list": lst, "page": pg, "pagecount": 1, "limit": 24, "total": len(lst)}

        if tid == "mix":
            if cat:
                return self._union_list(cat, pg)
            # 随机：拿一大把再打乱，跟「推荐」不重样
            items = self._pick(self._get("/web/api/mix", n=30), "list", "items") or []
            lst = self._cards(items)
            try:
                import random
                random.shuffle(lst)
            except Exception:
                pass
            if not lst:
                lst = self._empty_list()
            return {"list": lst, "page": pg, "pagecount": 9999, "limit": 24, "total": len(lst)}

        if tid.startswith("cat:"):
            return self._union_list(tid[4:], pg)

        plat = tid.split(":", 1)[1] if ":" in tid else tid
        d = self._get("/web/api/list", plat=plat, cat=cat, page=pg)
        items = self._pick(d, "list", "items") or []
        lst = self._cards(items, plat_fallback=plat)
        if not lst:
            lst = self._empty_list()
        return {"list": lst, "page": pg,
                "pagecount": 9999 if len(lst) >= 12 else pg,
                "limit": 24, "total": len(lst)}

    # ----------------------------------------------------------- detail 接口

    def detailContent(self, array):
        if isinstance(array, (list, tuple)):
            vid = array[0] if array else ""
        else:
            vid = array
        vid = str(vid or "")
        if vid.startswith("diag"):
            return {"list": [self._diag_detail(vid)]}
        if "|" not in vid:
            return {"list": []}
        plat, series_id = vid.split("|", 1)

        d = self._get("/web/api/series", plat=plat, id=series_id)
        if not d:
            return {"list": []}

        name = self._pick(d, "name", "title", "series_name") or ""
        cover = self._abs(self._pick(d, "cover", "pic", "coverUrl", "poster"))
        desc = self._pick(d, "desc", "intro", "introduction", "summary", "content")
        plat_name = self._pick(d, "plat_name", "plat") or plat
        eps = self._pick(d, "episodes", "episodeList", "list") or []
        # series 详情里不带分集时，走 /web/api/seasons 把选集取回来
        if not eps:
            try:
                sd = self._get("/web/api/seasons", plat=plat, id=series_id, name=name)
                eps = self._pick(sd, "list", "items", "seasons", "episodes") or []
            except Exception:
                eps = []
        total = self._pick(d, "total", "episodeCount", "totalEpisodes") or len(eps)

        plays = []
        for i, ep in enumerate(eps):
            if isinstance(ep, dict):
                en = self._pick(ep, "name", "title", "episodeName") or ("第%d集" % (i + 1))
                idx = self._pick(ep, "ep", "no", "index", "episode", "episodeNumber",
                                 "episode_number", "episode_no", "n")
                if idx == "" or idx is None:
                    idx = i + 1
                try:
                    idx = int(idx)
                except Exception:
                    idx = i + 1
            else:
                en = "第%d集" % (i + 1)
                idx = i + 1
            en = str(en).replace("$", " ").replace("#", " ")
            plays.append("%s$%s|%s|%d" % (en, plat, series_id, idx))

        if not plays:
            plays = ["正片$%s|%s|1" % (plat, series_id)]

        return {"list": [{
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": cover,
            "type_name": str(plat_name),
            "vod_year": "",
            "vod_area": "",
            "vod_remarks": ("%s集" % total) if total else "",
            "vod_actor": "",
            "vod_director": "",
            "vod_content": str(desc or name),
            "vod_play_from": str(plat_name or "juku"),
            "vod_play_url": "#".join(plays),
        }]}

    # ----------------------------------------------------------- search 接口

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        key = str(key or "").strip()
        t0 = time.time()
        # 搜索单独给短一点超时 + 一次快失败重试：壳等搜索通常比等列表短
        d = self._get("/web/api/search", wd=key, page=pg, tmo=12, retry=1)
        items = self._pick(d, "list", "items") or []
        lst = self._cards(items)
        _log("搜索「%s」第%s页 → %d 条（%.1fs）" % (key, pg, len(lst), time.time() - t0))
        if not lst:
            lst = [self._empty_card("没搜到：%s" % (key or "(空)"), self._search_hint(key, pg))]
        return {"list": lst, "page": pg, "pagecount": 9999 if len(lst) >= 12 else pg,
                "limit": 24, "total": len(lst)}

    def _search_hint(self, key, pg):
        if self._base_missing():
            lines = self._diag_lines(probe=True)
            return "后台没连上，所以搜不了。\n\n" + "\n".join(lines)
        base = self.base or ""
        return ("搜索词：%s（第 %s 页）\n"
                "接口：%s/web/api/search?wd=%s&page=%s\n"
                "返回：0 条\n"
                "最后错误：%s\n\n"
                "两种可能：\n"
                "1. 后台在跑，但库里真没有含这个词的片子 → 换个更短/更通用的词（比如只留两个字）\n"
                "2. 后台在跑但这里是空的 → 把这张卡截图发给助手，带上看下面这行\n"
                "本次接口地址：%s"
                % (key, pg, base, key, pg, self._last_err or "无",
                   "%s/web/api/search?wd=%s&page=%s" % (base, key, pg)))

    # ----------------------------------------------------------- player 接口

    def playerContent(self, flag, vid, vipFlags=None):
        vid = str(vid or "")
        parts = vid.split("|")
        if len(parts) < 3:
            return {"parse": 1, "playUrl": "", "url": "", "header": ""}
        plat, series_id, ep = parts[0], parts[1], parts[2]

        d = self._get("/web/api/episode", plat=plat, id=series_id, ep=ep)
        play = self._pick(d, "play", "url", "playUrl", "src")
        is_m3u8 = d.get("m3u8")

        if not play:
            return {"parse": 1, "playUrl": "", "url": "", "header": ""}

        url = self._abs(play)
        low = url.lower()
        direct = bool(is_m3u8) or low.endswith(".m3u8") or ".m3u8?" in low or low.endswith(".mp4") or ".mp4?" in low

        header = json.dumps({
            "User-Agent": UA,
            "Referer": (self.base + "/") if self.base else "",
        })
        if direct:
            return {"parse": 0, "playUrl": "", "url": url, "header": header}
        # 网页地址 → 交给壳的解析通道
        return {"parse": 1, "playUrl": "", "url": url, "header": header}

    # ------------------------------------------------------------------ 诊断

    def _base_missing(self):
        if not self.base:
            self.base = self._resolve_base(self.ext)
        return not self.base

    def _conn_state(self):
        """"ok" 后台真连上 / "noanswer" 端口开着但接口没答话 / "down" 完全没找到后台。"""
        if self._base_missing():
            return "down"
        return "ok" if self._api_ok else "noanswer"

    def _diag_lines(self, probe=False):
        lines = []
        lines.append("juku py 源 %s" % VERSION)
        _st = {"ok": "后台已连上", "noanswer": "端口开着但接口没答话", "down": "没找到后台"}
        lines.append("连接状态：%s" % _st.get(self._conn_state(), "?"))
        lines.append("后台地址：%s" % (self.base or "没探到"))
        lines.append("来源：%s" % (self._base_src or "-"))
        lines.append("最后错误：%s" % (self._last_err or "无"))
        if self._cats_src:
            lines.append("分类来源：%s%s" % (self._cats_src,
                                        ("（%s）" % self._cats_note) if self._cats_note else ""))
        if probe and self.base:
            lines.append("")
            lines.append("—— 接口回包快照 ——")
            probes = (("/web/api/platforms", {}, "平台列表"),
                      ("/web/api/feed", {"n": 5, "blk": 1}, "推荐流"))
            for path, params, label in probes:
                qs = ("?" + urlencode(params)) if params else ""
                try:
                    raw = self._raw(self.base + path + qs, PROBE_HTTP_TIMEOUT)
                    lines.append("%s  [%s]" % (path + qs, label))
                    lines.append("  原文：%s" % _short(json.dumps(raw, ensure_ascii=False), 220))
                    uw = _unwrap(raw)
                    if isinstance(uw, dict):
                        cnt = ""
                        for k in ("platforms", "items", "list", "unions"):
                            v = uw.get(k)
                            if isinstance(v, list):
                                cnt = " %s=%d 条" % (k, len(v))
                                break
                        lines.append("  剥壳后字段：%s%s"
                                     % (",".join(list(uw.keys())[:8]) or "空对象", cnt))
                    elif isinstance(uw, list):
                        lines.append("  剥壳后：list %d 条" % len(uw))
                except Exception as e:
                    lines.append("%s → 出错：%s" % (path, _short(e, 90)))
        lines.append("")
        lines.append("—— 探测过程 ——")
        for t in self._trace[-12:]:
            lines.append(t)
        lines.append("")
        lines.append("—— 怎么修 ——")
        lines.append("1. 先确认壳里那个 juku-node 服务真的在跑（面板上写“已就绪”不算，得真点了启动且没报错）")
        lines.append("2. 看后台日志：/storage/emulated/0/juku-node-fixed/data/juku-node.log")
        lines.append("3. 后台没起 → 启动它；起来了但端口不是 8998 → 看 juku.port 文件里那个数")
        lines.append("4. 上面「接口回包快照」把原文截图发给助手，能直接定位是哪一层不对")
        return lines

    def _diag_detail(self, vid="diag|0"):
        custom = self._card_text.get(str(vid))
        if custom:
            text = custom
        else:
            text = "\n".join(self._diag_lines(probe=(not self.base) or str(vid).endswith("1")))
        _log("输出诊断：%s" % text.replace("\n", " / ")[:600])
        return {
            "vod_id": str(vid),
            "vod_name": "juku 连接诊断",
            "vod_pic": "",
            "type_name": "诊断",
            "vod_year": "",
            "vod_area": "",
            "vod_remarks": "",
            "vod_actor": "",
            "vod_director": "",
            "vod_content": text,
            "vod_play_from": "juku",
            "vod_play_url": "诊断信息$%s|0" % str(vid),
        }

    def _empty_card(self, name, text, vid="diag|9"):
        """通用说明卡：列表里没东西时给一张能点开的卡，点开就是原因，不留白屏。"""
        self._card_text[str(vid)] = text
        return {
            "vod_id": str(vid),
            "vod_name": name,
            "vod_pic": "",
            "vod_remarks": "点开看原因",
        }

    def _status_card(self):
        """首页第一张永远是状态卡：一眼看出 py 版本 / 后台通没通 / 接口回包长什么样。"""
        if self._no_status:
            return None
        st = self._conn_state()
        if st != "ok":
            return {
                "vod_id": "diag|0",
                "vod_name": ("⚠ 后台没连上 · 点我看原因" if st == "down"
                             else "⚠ 后台端口开着、接口没答话 · 点我看原因"),
                "vod_pic": "",
                "vod_remarks": "py %s · 点开有诊断" % VERSION,
            }
        n = len(self._get_plats())
        note = {"缓存": "分类走缓存", "种子": "分类是内置种子", "实时": "分类实时"}.get(self._cats_src, "分类实时")
        u = len(self._unions or [])
        return {
            "vod_id": "diag|1",
            "vod_name": "%s 后台已连上 %s" % (("✅" if n else "⚡"), self.base.replace("http://", "")),
            "vod_pic": "",
            "vod_remarks": "py %s · %d 分类/%d 平台 · %s · 点我看接口回包" % (VERSION, u, n, note),
        }

    def _empty_list(self):
        """列表空的时候：连不上后台就给诊断卡/说明卡，不留一片白。"""
        if self._conn_state() != "ok":
            c = self._status_card()
            if c:
                c["vod_remarks"] = "点开看原因"
                return [c]
            return []
        return [self._empty_card("这一栏暂时没内容 · 点我看原因",
                                 self._search_hint("(列表)", "1"))]

    # ------------------------------------------------------------------ 辅助

    def _get_plats(self):
        if self._plats is not None:
            return self._plats
        d = self._get("/web/api/platforms", tmo=HOME_TIMEOUT, retry=1)
        plats = self._pick(d, "platforms", "list", "items") or []
        out = []
        for p in plats:
            if isinstance(p, str):
                out.append({"name": p, "key": p})
            elif isinstance(p, dict):
                out.append(p)
        self._plats = out
        return out

    def _get_unions(self):
        if self._unions is not None:
            return self._unions
        d = self._get("/web/api/unions", tmo=HOME_TIMEOUT, retry=1)
        us = self._pick(d, "unions", "list", "items") or []
        out = [u for u in us if isinstance(u, dict)]
        self._unions = out
        return out

    def _cats_of(self, p):
        for k in ("cats", "categories", "catList", "classes"):
            v = p.get(k)
            if isinstance(v, list) and v:
                out = []
                for c in v:
                    if isinstance(c, str):
                        out.append({"name": c, "cat": c})
                    elif isinstance(c, dict):
                        out.append(c)
                return out
        return []

    # 本地调试入口
    def localProxy(self, param):
        return None


if __name__ == "__main__":
    s = Spider().init("")
    print("版本 =", VERSION)
    print("base =", s.base, "| 来源 =", s._base_src)
    print("--- 探测过程 ---")
    for t in s._trace:
        print("  ", t)
    hc = s.homeContent(True)
    print("class count =", len(hc["class"]))
    for c in hc["class"][:8]:
        print("  ", c)
    print("--- 首页列表 ---")
    for it in s.homeVideoContent().get("list", [])[:5]:
        print("  ", it)

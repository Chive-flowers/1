# -*- coding: utf-8 -*-
# Jable.tv 四壳 Python 源
# 主站 Cloudflare Turnstile 强盾；默认走反代域名替换（铁律15）
# 结构：/categories/ /latest-updates/ /videos/{slug}/ /search/{kw}/
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        def getName(self):
            return ""

        def init(self, extend=""):
            pass


class Spider(_BaseSpider):
    def __init__(self):
        self.rawSite = "https://jable.tv"
        # 默认反代（域名替换式）；ext.direct=true 可强制直连
        self.proxy = "https://xsz-shared-proxy.97471201.workers.dev"
        self.siteUrl = self.proxy
        self.host = self.siteUrl
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.headers = {
            "User-Agent": self.ua,
            "Referer": self.rawSite + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def getName(self):
        return "Jable"

    def init(self, extend=""):
        # extend 可为 JSON: {"proxy":"...","direct":true,"siteUrl":"..."}
        if not extend:
            return
        try:
            import json

            conf = json.loads(extend) if isinstance(extend, str) else (extend or {})
        except Exception:
            conf = {}
        if conf.get("direct") in (True, "true", "1", 1):
            self.siteUrl = self.rawSite
            self.host = self.rawSite
            return
        if conf.get("proxy"):
            self.proxy = str(conf["proxy"]).rstrip("/")
            self.siteUrl = self.proxy
            self.host = self.siteUrl
        elif conf.get("siteUrl"):
            self.siteUrl = str(conf["siteUrl"]).rstrip("/")
            self.host = self.siteUrl

    def getDependence(self):
        return ""

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return any(x in u for x in (".m3u8", ".mp4", ".mkv", ".flv"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return [404, "text/plain", b""]

    def homeContent(self, filter):
        classes = [
            {"type_id": "/latest-updates/", "type_name": "最近更新"},
            {"type_id": "/categories/chinese-subtitle/", "type_name": "中文字幕"},
            {"type_id": "/categories/uncensored-leak/", "type_name": "无码流出"},
            {"type_id": "/categories/new-release/", "type_name": "新作上市"},
            {"type_id": "/categories/uncensored/", "type_name": "无码"},
            {"type_id": "/categories/sfj-av/", "type_name": "素人人妻"},
            {"type_id": "/categories/exclusive/", "type_name": "独家"},
            {"type_id": "/hot/", "type_name": "热门"},
            {"type_id": "/categories/roleplay/", "type_name": "角色扮演"},
            {"type_id": "/categories/uniform/", "type_name": "制服丝袜"},
            {"type_id": "/categories/married-woman/", "type_name": "人妻"},
            {"type_id": "/categories/rape/", "type_name": "凌辱"},
            {"type_id": "/categories/creampie/", "type_name": "中出"},
            {"type_id": "/categories/big-tits/", "type_name": "巨乳"},
            {"type_id": "/categories/lesbian/", "type_name": "百合"},
            {"type_id": "/categories/anal/", "type_name": "肛交"},
        ]
        result = {"class": classes}
        if filter:
            result["filters"] = {c["type_id"]: [] for c in classes}
        return result

    def homeVideoContent(self):
        return self.categoryContent("/latest-updates/", "1", False, {})

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        tid = str(tid or "/latest-updates/")
        if not tid.startswith("/"):
            tid = "/" + tid
        if not tid.endswith("/"):
            tid = tid + "/"
        if page <= 1:
            path = tid
        else:
            path = "%s%d/" % (tid, page)
        url = self._abs(path)
        html = self._fetch(url)
        items = self._parse_list(html)
        has_next = bool(html and (len(items) >= 20 or ("/%d/" % (page + 1)) in html))
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 24),
            "total": pagecount * max(1, len(items) or 24),
        }

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        q = str(key or "").strip()
        if not q:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        if page <= 1:
            path = "/search/%s/" % self._quote(q)
        else:
            path = "/search/%s/%d/" % (self._quote(q), page)
        html = self._fetch(self._abs(path))
        items = self._parse_list(html)
        has_next = bool(html and len(items) >= 20)
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 24),
            "total": pagecount * max(1, len(items) or 24),
        }

    def detailContent(self, ids):
        vid = str(ids[0] if ids else "").strip()
        if not vid:
            return {"list": []}
        if not vid.startswith("/"):
            # 可能是 slug 或完整 path
            if not vid.startswith("videos/"):
                vid = "/videos/%s/" % vid.strip("/")
            else:
                vid = "/" + vid.strip("/") + ("/" if not vid.endswith("/") else "")
        url = self._abs(vid)
        html = self._fetch(url)
        if not html:
            return {"list": []}
        name = self._meta_title(html) or vid.strip("/").split("/")[-1]
        pic = self._first(
            self._findall(
                r'(?:og:image"|poster"\s*content="|data-src=")(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)',
                html,
            )
            or self._findall(
                r'(https://[^"]*assets-cdn\.jable\.tv/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)',
                html,
            )
        )
        # hlsUrl 常见写法
        m3u8s = []
        for pat in (
            r"hlsUrl\s*=\s*['\"](https?://[^'\"]+\.m3u8[^'\"]*)['\"]",
            r"var\s+hlsUrl\s*=\s*['\"]([^'\"]+)['\"]",
            r"['\"](https?://[^'\"]+\.m3u8[^'\"]*)['\"]",
            r'source\s+src=["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        ):
            m3u8s.extend(self._findall(pat, html))
        seen = set()
        play = []
        for i, u in enumerate(m3u8s):
            u = u.replace("\\u0026", "&").replace("\\/", "/")
            if u in seen or ".m3u8" not in u:
                continue
            seen.add(u)
            play.append("%s$%s" % ("播放%d" % (i + 1) if i else "正片", u))
        if not play:
            return {
                "list": [
                    {
                        "vod_id": vid,
                        "vod_name": name,
                        "vod_pic": pic or "",
                        "vod_content": "未解析到播放地址（可能需过盾/换代理）",
                        "vod_play_from": "提示",
                        "vod_play_url": "暂无$0",
                    }
                ]
            }
        return {
            "list": [
                {
                    "vod_id": vid,
                    "vod_name": name,
                    "vod_pic": pic or "",
                    "vod_content": name,
                    "vod_play_from": "Jable",
                    "vod_play_url": "#".join(play),
                }
            ]
        }

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url or url == "0":
            return {"parse": 0, "jx": 0, "url": "", "msg": "无播放地址"}
        if url.startswith("//"):
            url = "https:" + url
        headers = {
            "User-Agent": self.ua,
            "Referer": self.rawSite + "/",
            "Origin": self.rawSite,
        }
        return {"parse": 0, "jx": 0, "url": url, "header": headers}

    # ---------- helpers ----------
    def _abs(self, path):
        """域名替换式：path 挂在 siteUrl 下；直连时挂在 rawSite"""
        if path.startswith("http"):
            # 把 rawSite 替换为 siteUrl
            if path.startswith(self.rawSite):
                return self.siteUrl + path[len(self.rawSite) :]
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.siteUrl.rstrip("/") + path

    def _parse_list(self, html):
        if not html or "Just a moment" in (html[:800] if html else ""):
            return []
        items = []
        seen = set()
        # 标准卡片：a.img-box[href=/videos/xxx/] + img + title
        for m in self._finditer(
            r'href="(/videos/[^"]+/)"[^>]*>[\s\S]{0,1200}?(?:data-src|src)="(https?://[^"]+)"[\s\S]{0,400}?title="([^"]*)"',
            html,
        ):
            path, pic, title = m.group(1), m.group(2), self._clean(m.group(3))
            if path in seen:
                continue
            seen.add(path)
            if self._skip_minor(title):
                continue
            items.append(
                {
                    "vod_id": path,
                    "vod_name": title or path.strip("/").split("/")[-1],
                    "vod_pic": pic,
                    "vod_remarks": "",
                }
            )
        if not items:
            for m in self._finditer(r'href="(/videos/[^"]+/)"', html):
                path = m.group(1)
                if path in seen:
                    continue
                seen.add(path)
                slug = path.strip("/").split("/")[-1]
                items.append(
                    {
                        "vod_id": path,
                        "vod_name": slug,
                        "vod_pic": "",
                        "vod_remarks": "",
                    }
                )
        return items

    @staticmethod
    def _skip_minor(name):
        n = (name or "").lower()
        keys = ("萝莉", "幼女", "小学生", "loli", "underage", "未成年", "女童")
        return any(k in n for k in keys)

    def _meta_title(self, html):
        for pat in (
            r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
            r"<title>([^<]+)</title>",
            r'<h4[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h4>',
        ):
            m = self._search(pat, html)
            if m:
                t = self._clean(m)
                for s in (" - Jable.TV", " | Jable", " - jable"):
                    if t.endswith(s):
                        t = t[: -len(s)]
                return t.strip()
        return ""

    def _fetch(self, url):
        # 1) curl
        try:
            import subprocess

            cmd = [
                "curl",
                "-sL",
                "--max-time",
                "25",
                "-A",
                self.ua,
                "-e",
                self.rawSite + "/",
                "-H",
                "Accept-Language: zh-CN,zh;q=0.9",
                url,
            ]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
            text = out.decode("utf-8", "ignore")
            if text and "Just a moment" not in text[:600] and len(text) > 800:
                return text
        except Exception:
            pass
        # 2) curl_cffi
        try:
            from curl_cffi import requests as cffi

            r = cffi.get(
                url,
                impersonate="chrome124",
                headers=self.headers,
                timeout=25,
            )
            if r.status_code == 200 and r.text and "Just a moment" not in r.text[:600]:
                return r.text
        except Exception:
            pass
        # 3) requests
        try:
            import requests
            import urllib3

            urllib3.disable_warnings()
            r = requests.get(url, headers=self.headers, timeout=20, verify=False)
            if r.status_code == 200 and r.text and "Just a moment" not in r.text[:600]:
                return r.text
        except Exception:
            pass
        return ""

    @staticmethod
    def _quote(s):
        try:
            from urllib.parse import quote

            return quote(str(s), safe="")
        except Exception:
            return str(s)

    @staticmethod
    def _clean(s):
        import re

        s = re.sub(r"<[^>]+>", "", str(s or ""))
        return (
            s.replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&#039;", "'")
            .strip()
        )

    @staticmethod
    def _search(pat, text):
        import re

        m = re.search(pat, text or "", re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _findall(pat, text):
        import re

        return re.findall(pat, text or "", re.I)

    @staticmethod
    def _finditer(pat, text):
        import re

        return list(re.finditer(pat, text or "", re.I | re.S))

    @staticmethod
    def _first(arr):
        return arr[0] if arr else ""

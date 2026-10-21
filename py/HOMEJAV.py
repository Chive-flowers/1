# -*- coding: utf-8 -*-
# HOMEJAV https://homejav.com/cn
# 列表/分类/搜索 HTML 解析；播放主清单 /media/videos/v_{hash}.m3u8 → 多清晰度
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
        self.host = "https://homejav.com"
        self.lang = "cn"
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def getName(self):
        return "HOMEJAV"

    def init(self, extend=""):
        pass

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
            {"type_id": "videos/japanese", "type_name": "有码"},
            {"type_id": "videos/uncensored-leak", "type_name": "无码流出"},
            {"type_id": "category/fc2ppv", "type_name": "FC2"},
            {"type_id": "category/1pondo", "type_name": "一本道"},
            {"type_id": "category/caribbeancom", "type_name": "加勒比"},
            {"type_id": "category/heyzo", "type_name": "HEYZO"},
            {"type_id": "category/siro", "type_name": "SIRO"},
            {"type_id": "category/luxu", "type_name": "LUXU"},
            {"type_id": "category/gana", "type_name": "GANA"},
            {"type_id": "category/s-cute", "type_name": "S-CUTE"},
            {"type_id": "category/prestigepremium", "type_name": "PRESTIGE"},
            {"type_id": "category/naughty0930", "type_name": "H0930"},
            {"type_id": "category/naughty4610", "type_name": "H4610"},
            {"type_id": "category/pacopacomama", "type_name": "pacopacomama"},
            {"type_id": "category/10musume", "type_name": "10musume"},
            {"type_id": "category/xxx-av", "type_name": "XXX-AV"},
        ]
        result = {"class": classes}
        if filter:
            result["filters"] = {c["type_id"]: [] for c in classes}
        return result

    def homeVideoContent(self):
        return self.categoryContent("videos/japanese", "1", False, {})

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        tid = str(tid or "videos/japanese").lstrip("/")
        path = "/%s/%s" % (self.lang, tid)
        if page > 1:
            url = "%s%s?page=%s" % (self.host, path, page)
        else:
            url = self.host + path
        html = self._fetch(url)
        items = self._parse_list(html)
        has_next = bool(
            html
            and (
                ("page=%s" % (page + 1)) in html
                or ('page/%s' % (page + 1)) in html
                or len(items) >= 10
            )
        )
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 12),
            "total": pagecount * max(1, len(items) or 12),
        }

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        q = str(key or "").strip()
        if not q:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        path = "/%s/search/%s" % (self.lang, self._quote(q))
        if page > 1:
            url = "%s%s?page=%s" % (self.host, path, page)
        else:
            url = self.host + path
        html = self._fetch(url)
        items = self._parse_list(html)
        has_next = bool(html and (("page=%s" % (page + 1)) in html or len(items) >= 10))
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 12),
            "total": pagecount * max(1, len(items) or 12),
        }

    def detailContent(self, ids):
        raw = str(ids[0] if ids else "").strip()
        if not raw:
            return {"list": []}
        # vod_id 格式: hash 或 hash/slug
        if "/" in raw:
            vid, slug = raw.split("/", 1)
        else:
            vid, slug = raw, raw
        url = "%s/%s/video/%s/%s" % (self.host, self.lang, vid, slug)
        html = self._fetch(url)
        name = self._meta_title(html) or slug
        pic = self._first(
            self._findall(r'(https://img\.homejav\.com/[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)', html)
        )
        # 主清单
        master = "%s/media/videos/v_%s.m3u8" % (self.host, vid)
        play_from = []
        play_url = []
        qualities = self._parse_master(master)
        play_from.append("HOMEJAV")
        eps = ["自适应$%s" % master]
        for label, u in qualities:
            if u != master:
                eps.append("%s$%s" % (label, u))
        if len(eps) == 1:
            # 主清单可能直接可播或仅单码率
            body = self._fetch(master)
            if not (body and "#EXTM3U" in body):
                play_from = ["提示"]
                eps = ["暂无$0"]
        play_url.append("#".join(eps))
        if not play_from:
            return {
                "list": [
                    {
                        "vod_id": raw,
                        "vod_name": name,
                        "vod_pic": pic or "",
                        "vod_content": "未解析到播放地址",
                        "vod_play_from": "提示",
                        "vod_play_url": "暂无$0",
                    }
                ]
            }
        return {
            "list": [
                {
                    "vod_id": raw,
                    "vod_name": name,
                    "vod_pic": pic or "",
                    "vod_content": name,
                    "vod_play_from": "$$$".join(play_from),
                    "vod_play_url": "$$$".join(play_url),
                }
            ]
        }

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url or url == "0":
            return {"parse": 0, "jx": 0, "url": "", "msg": "无播放地址"}
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = self.host + url
        headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Origin": self.host,
        }
        return {"parse": 0, "jx": 0, "url": url, "header": headers}

    # ---------- helpers ----------
    def _parse_list(self, html):
        if not html:
            return []
        items = []
        seen = set()
        # 卡片：href 在前，封面在 data-src，标题在 alt / title
        for m in self._finditer(
            r'href="https://homejav\.com/%s/video/([a-f0-9]+)/([^"]+)"[\s\S]{0,1500}?data-src="(https://img\.homejav\.com[^"]+)"[\s\S]{0,400}?alt="([^"]*)"'
            % self.lang,
            html,
        ):
            vid, slug, pic, alt = m.group(1), m.group(2), m.group(3), self._clean(m.group(4))
            if vid in seen:
                continue
            seen.add(vid)
            name = alt if alt and alt != "user.username" else slug.replace("-", " ").upper()
            items.append(
                {
                    "vod_id": "%s/%s" % (vid, slug),
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_remarks": "",
                }
            )
        # 宽松回落：仅 hash+slug
        if not items:
            for m in self._finditer(
                r'href="https://homejav\.com/%s/video/([a-f0-9]+)/([^"]+)"' % self.lang,
                html,
            ):
                vid, slug = m.group(1), m.group(2)
                if vid in seen:
                    continue
                seen.add(vid)
                items.append(
                    {
                        "vod_id": "%s/%s" % (vid, slug),
                        "vod_name": slug.replace("-", " ").upper(),
                        "vod_pic": "",
                        "vod_remarks": "",
                    }
                )
            # 补 data-src 图
            for m in self._finditer(
                r'href="https://homejav\.com/%s/video/([a-f0-9]+)/[^"]+"[\s\S]{0,1500}?data-src="(https://img\.homejav\.com[^"]+)"'
                % self.lang,
                html,
            ):
                vid, pic = m.group(1), m.group(2)
                for it in items:
                    if it["vod_id"].startswith(vid + "/") and not it["vod_pic"]:
                        it["vod_pic"] = pic
            # 补 title=
            for m in self._finditer(
                r'href="https://homejav\.com/%s/video/([a-f0-9]+)/[^"]+"[^>]*title="([^"]+)"'
                % self.lang,
                html,
            ):
                vid, title = m.group(1), self._clean(m.group(2))
                for it in items:
                    if it["vod_id"].startswith(vid + "/") and title:
                        it["vod_name"] = title
        out = []
        for it in items:
            if self._skip_minor(it["vod_name"]):
                continue
            out.append(it)
        return out

    def _parse_master(self, master_url):
        """解析主清单，返回 [(label, url), ...]"""
        body = self._fetch(master_url)
        if not body or "#EXTM3U" not in body:
            return []
        result = []
        lines = body.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                name = "多码率"
                m = self._search(r'NAME="([^"]+)"', line)
                if m:
                    name = m
                else:
                    r = self._search(r"RESOLUTION=(\d+x\d+)", line)
                    if r:
                        name = r
                if i + 1 < len(lines):
                    u = lines[i + 1].strip()
                    if u and not u.startswith("#"):
                        if u.startswith("//"):
                            u = "https:" + u
                        elif u.startswith("/"):
                            u = self.host + u
                        result.append((name, u))
                i += 2
                continue
            i += 1
        if not result and "#EXTINF" in body:
            result.append(("正片", master_url))
        return result

    @staticmethod
    def _skip_minor(name):
        n = (name or "").lower()
        keys = ("萝莉", "幼女", "小学生", "loli", "underage", "未成年", "女童")
        return any(k in n for k in keys)

    def _meta_title(self, html):
        for pat in (
            r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
            r"<title>([^<]+)</title>",
        ):
            m = self._search(pat, html)
            if m:
                t = self._clean(m)
                for suffix in (" - HOMEJAV", " | HOMEJAV", " - homejav"):
                    if t.endswith(suffix):
                        t = t[: -len(suffix)]
                return t.strip()
        return ""

    def _fetch(self, url):
        # 1) curl CLI（开发机对 CF 更稳）
        try:
            import subprocess

            cmd = [
                "curl", "-sL", "--max-time", "25",
                "-A", self.ua,
                "-e", self.host + "/",
                "-H", "Accept-Language: zh-CN,zh;q=0.9",
                url,
            ]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
            text = out.decode("utf-8", "ignore")
            if text and "Just a moment" not in text[:500] and len(text) > 500:
                return text
        except Exception:
            pass
        # 2) curl_cffi 指纹
        try:
            from curl_cffi import requests as cffi

            r = cffi.get(url, impersonate="chrome124", headers=self.headers, timeout=25)
            if r.status_code == 200 and r.text and "Just a moment" not in r.text[:500]:
                return r.text
        except Exception:
            pass
        # 3) requests 回落
        try:
            import requests
            import urllib3

            urllib3.disable_warnings()
            r = requests.get(url, headers=self.headers, timeout=20, verify=False)
            if r.status_code == 200 and r.text:
                r.encoding = r.apparent_encoding or "utf-8"
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
            .replace("&#039;", "'")
            .replace("&quot;", '"')
            .replace("&lt;", "<")
            .replace("&gt;", ">")
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

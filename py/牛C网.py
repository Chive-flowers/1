# coding=utf-8
try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider:
        def getName(self): return ""
        def init(self, extend=""): pass
        def homeContent(self, filter=None): return {}
        def homeVideoContent(self): return {}
        def categoryContent(self, tid, pg, filter=None, extend=None): return {}
        def detailContent(self, ids): return {}
        def searchContent(self, key, quick=False, pg="1"): return {}
        def playerContent(self, flag, id, vipFlags=None): return {}
        def localProxy(self, param): return [404, "text/plain", ""]
        def getDependence(self): return ""
        def isVideoFormat(self, url): return False
        def manualVideoCheck(self): return False

import re, time, json, urllib.request
from urllib.parse import quote, urljoin, urlparse

class Spider(BaseSpider):
    def getName(self):
        return "牛C网"

    def init(self, extend=""):
        self.host = "https://rulel.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }

    def getDependence(self):
        return ""

    def _get(self, path, headers=None, retry=2):
        url = path if path.startswith("http") else self.host + path
        hdrs = headers or self.headers
        for i in range(retry + 1):
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=18) as resp:
                    return resp.read().decode("utf-8", "ignore")
            except Exception:
                if i == retry:
                    return ""
                time.sleep(0.4)
        return ""

    def _unescape(self, s):
        try:
            import html as H
            return H.unescape(s or "")
        except Exception:
            return s or ""

    def homeContent(self, filter=None):
        classes = [
            {"type_id": "pikpak", "type_name": "PIKPAK资源"},
            {"type_id": "pikpak/lada-uncorrected", "type_name": "LADA破解"},
            {"type_id": "apexdrive", "type_name": "ApexDrive"},
            {"type_id": "magnet", "type_name": "磁力总汇"},
            {"type_id": "pikpak/jinricp", "type_name": "Jinricp"},
            {"type_id": "pikpak/pandaclass", "type_name": "PandaClass"},
            {"type_id": "sexygirl", "type_name": "美女写真"},
            {"type_id": "cosplay", "type_name": "Cosplay"},
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("pikpak", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = str(pg or "1")
        tid = str(tid).strip("/")
        if pg == "1":
            path = f"/{tid}/"
        else:
            path = f"/{tid}/page/{pg}/"
        html = self._get(path)
        if not html:
            return {"page": int(pg), "pagecount": 1, "limit": 0, "total": 0, "list": []}
        videos = self._parse_list(html)
        pagecount = int(pg)
        pages = re.findall(r"/%s/page/(\d+)/" % re.escape(tid), html)
        if not pages:
            pages = re.findall(r"/page/(\d+)/", html)
        nums = [int(x) for x in pages if str(x).isdigit() and int(x) < 10000]
        if nums:
            pagecount = max(nums)
        elif len(videos) >= 8:
            pagecount = int(pg) + 1
        return {
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 12,
            "total": pagecount * 12,
            "list": videos,
        }

    def _parse_list(self, html):
        videos = []
        seen = set()
        for m in re.finditer(
            r'class="post-title"[^>]*>\s*<a[^>]+href="https://rulel\.com/(\d+)\.html"[^>]*>([^<]+)',
            html,
        ):
            pid = m.group(1)
            if pid in seen:
                continue
            seen.add(pid)
            name = self._unescape(m.group(2)).strip()
            # cover: look back for thumbnail data-src
            chunk = html[max(0, m.start() - 8000):m.start()]
            pm = re.findall(r'data-src="(https?://[^"]+)"', chunk)
            pic = pm[-1] if pm else ""
            videos.append({
                "vod_id": pid,
                "vod_name": name or pid,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        if not videos:
            for m in re.finditer(r'href="https://rulel\.com/(\d+)\.html"[^>]*>\s*([^<\n]{6,100})', html):
                pid, name = m.group(1), self._unescape(m.group(2)).strip()
                if pid in seen or name.startswith("http"):
                    continue
                seen.add(pid)
                videos.append({
                    "vod_id": pid,
                    "vod_name": name,
                    "vod_pic": "",
                    "vod_remarks": "",
                })
        return videos

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        pid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        html = self._get(f"/{pid}.html")
        if not html:
            return {"list": []}
        name = ""
        m = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        if m:
            name = self._unescape(m.group(1)).strip()
        if not name:
            m = re.search(r"<title>([^<]+)</title>", html)
            if m:
                name = self._unescape(m.group(1)).split(" - ")[0].strip()
        pic = ""
        m = re.search(r'data-src="(https?://[^"]+)"', html)
        if m:
            pic = m.group(1)
        # play sources: upload18 embeds
        embeds = list(dict.fromkeys(re.findall(r'https?://upload18\.org/play/index/([a-zA-Z0-9]+)', html)))
        if embeds:
            play_from = "在线播放"
            play_url = "#".join([f"线路{i+1}${pid}|{eid}" for i, eid in enumerate(embeds)])
        else:
            # fallback: show post only
            play_from = "在线播放"
            play_url = f"正片${pid}"
        return {
            "list": [{
                "vod_id": pid,
                "vod_name": name or pid,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": play_from,
                "vod_play_url": play_url,
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        pg = str(pg or "1")
        q = quote(key)
        path = f"/?s={q}" if pg == "1" else f"/page/{pg}/?s={q}"
        html = self._get(path)
        videos = self._parse_list(html or "")
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": 1 if len(videos) < 8 else int(pg) + 1,
            "limit": 12,
            "total": len(videos),
        }

    def _resolve_embed(self, eid):
        html = self._get(
            f"https://upload18.org/play/index/{eid}",
            headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        if not html:
            return ""
        m = re.search(r"window\.PLAYER_CONFIG\s*=\s*(\{.+?\})\s*;", html, re.S)
        if not m:
            return ""
        try:
            cfg = json.loads(m.group(1))
        except Exception:
            return ""
        url = (cfg.get("m3u8") or "").replace("\\/", "/")
        if "\\u" in url:
            try:
                url = url.encode("utf-8").decode("unicode_escape")
            except Exception:
                url = url.replace("\\u0026", "&")
        return url.replace("\\u0026", "&")

    def _fetch_media(self, url):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": "https://upload18.org/",
                "Origin": "https://upload18.org",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=18) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    def _resolve_m3u8(self, url):
        if not url or ".m3u8" not in url and "/m/" not in url:
            return url
        body = self._fetch_media(url)
        if not body:
            return url
        if "#EXTINF" in body and "#EXT-X-STREAM-INF" not in body:
            return url
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and (".m3u8" in line or line.startswith("http")):
                if line.startswith("http") and ".m3u8" in line:
                    return line
                if line.startswith("http"):
                    continue
                if ".m3u8" in line:
                    return urljoin(url, line)
        return url

    def playerContent(self, flag, id, vipFlags=None):
        raw = id or ""
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": "https://upload18.org/",
            "Origin": "https://upload18.org",
        }
        url = ""
        if raw.startswith("http"):
            url = raw
        elif "|" in raw:
            pid, eid = raw.split("|", 1)
            url = self._resolve_embed(eid.strip())
        else:
            # load post and first embed
            html = self._get(f"/{raw}.html")
            embeds = re.findall(r'upload18\.org/play/index/([a-zA-Z0-9]+)', html or "")
            if embeds:
                url = self._resolve_embed(embeds[0])
        if url:
            url = self._resolve_m3u8(url)
        if not url:
            return {"parse": 0, "jx": 0, "url": "", "header": header}
        if (".m3u8" in url or "/m/" in url) and hasattr(self, "getProxyUrl") and self.getProxyUrl():
            base = self.getProxyUrl()
            proxy = base + ("&" if "?" in base else "?") + "url=" + quote(url, safe="") + "&type=m3u8"
            return {"parse": 0, "jx": 0, "url": proxy, "header": header}
        return {"parse": 0, "jx": 0, "url": url, "header": header}

    def localProxy(self, param):
        try:
            url = param.get("url") if isinstance(param, dict) else str(param or "")
            if not url:
                return [404, "text/plain", ""]
            body = self._fetch_media(url)
            if not body:
                return [502, "text/plain", "fetch failed"]
            if "#EXT-X-STREAM-INF" in body and "#EXTINF" not in body:
                for line in body.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and ".m3u8" in line:
                        sub = line if line.startswith("http") else urljoin(url, line)
                        body = self._fetch_media(sub)
                        url = sub
                        break
            cleaned = self._clean_m3u8(body or "", url)
            return [200, "application/vnd.apple.mpegurl", cleaned]
        except Exception as e:
            return [500, "text/plain", str(e)[:200]]

    def _clean_m3u8(self, content, base_url=""):
        if not content or "#EXTM3U" not in content:
            return content
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        base_dir = base_url.rsplit("/", 1)[0] + "/"
        out = []
        for line in content.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#EXT-X-KEY"):
                mk = re.search(r'URI="([^"]+)"', s)
                if mk:
                    key_uri = mk.group(1)
                    if key_uri.startswith("/"):
                        s = s.replace(f'URI="{key_uri}"', f'URI="{base_origin + key_uri}"')
                    elif not key_uri.startswith("http"):
                        s = s.replace(f'URI="{key_uri}"', f'URI="{urljoin(base_dir, key_uri)}"')
                out.append(s)
            elif s.startswith("#"):
                out.append(s)
            else:
                if s.startswith("http"):
                    out.append(s)
                elif s.startswith("/"):
                    out.append(base_origin + s)
                else:
                    out.append(urljoin(base_dir, s))
        return "\n".join(out)

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|flv|mkv)(\?|$)", url or "", re.I) or "/m/" in (url or ""))

    def manualVideoCheck(self):
        return False

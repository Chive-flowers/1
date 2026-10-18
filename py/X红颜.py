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
        return "X红颜"

    def init(self, extend=""):
        self.host = "https://xhongyan.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }

    def getDependence(self):
        return ""

    def _get(self, path, retry=2):
        url = path if path.startswith("http") else self.host + path
        for i in range(retry + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read().decode("utf-8", "ignore")
            except Exception:
                if i == retry:
                    return ""
                time.sleep(0.3)
        return ""

    def _unescape(self, s):
        try:
            import html as H
            return H.unescape(s or "")
        except Exception:
            return s or ""

    def homeContent(self, filter=None):
        classes = [
            {"type_id": "1", "type_name": "onlyfans"},
            {"type_id": "2", "type_name": "网黄"},
            {"type_id": "3", "type_name": "品牌"},
            {"type_id": "4", "type_name": "业余"},
            {"type_id": "label/new", "type_name": "最新"},
            {"type_id": "label/hot", "type_name": "精选"},
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("1", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = str(pg or "1")
        tid = str(tid)
        if tid.startswith("label/"):
            path = f"/index.php/{tid}.html" if pg == "1" else f"/index.php/{tid}/page/{pg}.html"
        else:
            if pg == "1":
                path = f"/index.php/vod/type/id/{tid}.html"
            else:
                path = f"/index.php/vod/type/id/{tid}/page/{pg}.html"
        html = self._get(path)
        if not html:
            return {"page": int(pg), "pagecount": 1, "limit": 0, "total": 0, "list": []}
        videos = self._parse_list(html)
        pagecount = int(pg)
        pages = re.findall(r"/page/(\d+)\.html", html)
        if pages:
            pagecount = max(int(x) for x in pages if str(x).isdigit())
        elif len(videos) >= 12:
            pagecount = int(pg) + 1
        return {
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
            "list": videos,
        }

    def _parse_list(self, html):
        videos = []
        seen = set()
        for m in re.finditer(
            r'href="/index\.php/vod/play/id/(\d+)/sid/\d+/nid/\d+\.html"[^>]*>[\s\S]{0,500}?alt="([^"]*)"[\s\S]{0,200}?data-src="([^"]*)"',
            html,
        ):
            vid, name, pic = m.group(1), self._unescape(m.group(2)).strip(), m.group(3)
            if vid in seen:
                continue
            seen.add(vid)
            if pic and not pic.startswith("http"):
                pic = self.host + pic
            videos.append({
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        # alternate order: data-src before alt
        if not videos:
            for m in re.finditer(
                r'href="/index\.php/vod/play/id/(\d+)/[^"]*"[\s\S]{0,600}?data-src="([^"]*)"[^>]*alt="([^"]*)"',
                html,
            ):
                vid, pic, name = m.group(1), m.group(2), self._unescape(m.group(3)).strip()
                if vid in seen:
                    continue
                seen.add(vid)
                if pic and not pic.startswith("http"):
                    pic = self.host + pic
                videos.append({
                    "vod_id": vid,
                    "vod_name": name or vid,
                    "vod_pic": pic,
                    "vod_remarks": "",
                })
        if not videos:
            for m in re.finditer(r'/index\.php/vod/play/id/(\d+)/', html):
                vid = m.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                chunk = html[max(0, m.start() - 50):m.start() + 500]
                am = re.search(r'alt="([^"]+)"', chunk)
                pm = re.search(r'data-src="([^"]+)"', chunk)
                name = self._unescape(am.group(1)).strip() if am else vid
                pic = pm.group(1) if pm else ""
                if pic and not pic.startswith("http"):
                    pic = self.host + pic
                videos.append({
                    "vod_id": vid,
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_remarks": "",
                })
        return videos

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        html = self._get(f"/index.php/vod/play/id/{vid}/sid/1/nid/1.html")
        if not html:
            return {"list": []}
        name = ""
        m = re.search(r"<title>([^<]+)</title>", html)
        if m:
            name = self._unescape(m.group(1)).split(" - ")[0].strip()
        pic = ""
        m = re.search(r'(?:data-src|src)="(/upload/vod/[^"]+)"', html)
        if m:
            pic = self.host + m.group(1)
        return {
            "list": [{
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": "线路1",
                "vod_play_url": f"正片${vid}",
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        pg = str(pg or "1")
        q = quote(key)
        if pg == "1":
            path = f"/index.php/vod/search/wd/{q}.html"
        else:
            path = f"/index.php/vod/search/wd/{q}/page/{pg}.html"
        html = self._get(path)
        videos = self._parse_list(html or "")
        pages = re.findall(r"/page/(\d+)\.html", html or "")
        pagecount = max((int(x) for x in pages if str(x).isdigit()), default=1)
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
        }

    def _extract_url(self, html):
        m = re.search(r"var player_data\s*=\s*(\{.+?\})\s*;?\s*</script>", html or "", re.S)
        if m:
            try:
                d = json.loads(m.group(1))
                url = (d.get("url") or "").replace("\\/", "/")
                if url.startswith("/"):
                    url = self.host + url
                return url
            except Exception:
                u = re.search(r'"url"\s*:\s*"([^"]+)"', m.group(1))
                if u:
                    url = u.group(1).replace("\\/", "/")
                    if url.startswith("/"):
                        url = self.host + url
                    return url
        m = re.search(r'player_aaaa\s*=\s*(\{.+?\})\s*</script>', html or "", re.S)
        if m:
            try:
                return (json.loads(m.group(1)).get("url") or "").strip()
            except Exception:
                pass
        ms = re.findall(r'https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*', html or "")
        return ms[0] if ms else ""

    def playerContent(self, flag, id, vipFlags=None):
        raw = id or ""
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/",
        }
        if raw.startswith("http"):
            url = raw
        else:
            html = self._get(f"/index.php/vod/play/id/{quote(raw)}/sid/1/nid/1.html")
            url = self._extract_url(html)
        if not url:
            return {"parse": 0, "jx": 0, "url": "", "header": header}
        return {"parse": 0, "jx": 0, "url": url, "header": header}

    def localProxy(self, param):
        try:
            url = param.get("url") if isinstance(param, dict) else str(param or "")
            if not url:
                return [404, "text/plain", ""]
            req = urllib.request.Request(url, headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                ct = resp.headers.get("Content-Type") or "video/mp4"
            if url.endswith(".m3u8") or "mpegurl" in ct:
                body = data.decode("utf-8", "ignore")
                return [200, "application/vnd.apple.mpegurl", body]
            return [200, ct, data]
        except Exception as e:
            return [500, "text/plain", str(e)[:200]]

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|flv|mkv)(\?|$)", url or "", re.I))

    def manualVideoCheck(self):
        return False

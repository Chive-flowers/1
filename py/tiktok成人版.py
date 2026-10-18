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
        return "tiktok成人版"

    def init(self, extend=""):
        self.host = "https://tiktvod01.xyz"
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
            {"type_id": "domestic-media", "type_name": "国产传媒"},
            {"type_id": "japanese-av", "type_name": "日本AV"},
            {"type_id": "uncensored-video", "type_name": "无码视频"},
            {"type_id": "chinese-subtitles", "type_name": "中文字幕"},
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("domestic-media", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = str(pg or "1")
        tid = str(tid)
        if pg == "1":
            path = f"/vodtype/{tid}/"
        else:
            path = f"/vodtype/{tid}/page/{pg}/"
        html = self._get(path)
        if not html:
            return {"page": int(pg), "pagecount": 1, "limit": 0, "total": 0, "list": []}
        videos = self._parse_list(html)
        pagecount = int(pg)
        pages = re.findall(r"/vodtype/%s/page/(\d+)/" % re.escape(tid), html)
        if not pages:
            pages = re.findall(r'aria-label="Go to page (\d+)"', html)
        if pages:
            pagecount = max(int(x) for x in pages if str(x).isdigit())
        elif len(videos) >= 20:
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
        # VideoObject names in order
        names = re.findall(r'"@type"\s*:\s*"VideoObject"[\s\S]{0,400}?"name"\s*:\s*"((?:\\.|[^"\\])*)"', html)
        names = [self._unescape(n.encode().decode("unicode_escape") if "\\u" in n else n) for n in names]
        # unique slugs in order
        slugs = []
        for s in re.findall(r'/vodplay/([^"\'?\s]+)', html):
            s = s.strip("/")
            if s and s not in seen:
                seen.add(s)
                slugs.append(s)
        # covers by slug order from data-src near each first occurrence
        covers = {}
        for m in re.finditer(r'href="/vodplay/([^"]+)"[\s\S]{0,500}?data-src="(https?://[^"]+)"', html):
            slug = m.group(1)
            if slug not in covers:
                covers[slug] = m.group(2)
        if not covers:
            for m in re.finditer(r'data-src="(https?://[^"]+/upload/vod/[^"]+)"[\s\S]{0,300}?/vodplay/([^"\']+)', html):
                covers.setdefault(m.group(2), m.group(1))
        for i, slug in enumerate(slugs):
            name = names[i] if i < len(names) else slug
            if not name or len(name) < 2:
                name = slug
            pic = covers.get(slug, "")
            videos.append({
                "vod_id": slug,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        return videos

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        slug = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        html = self._get(f"/vodplay/{quote(slug)}")
        if not html:
            return {"list": []}
        name = ""
        m = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        if m:
            name = self._unescape(m.group(1)).strip()
        if not name or name in ("tiktok成人版",):
            # second h1 or title
            hs = re.findall(r"<h1[^>]*>([^<]+)</h1>", html)
            for h in hs:
                h = self._unescape(h).strip()
                if h and h != "tiktok成人版":
                    name = h
                    break
        if not name:
            m = re.search(r"<title>([^<]+)</title>", html)
            if m:
                name = self._unescape(m.group(1)).split("|")[0].strip()
        pic = ""
        m = re.search(r'data-src="(https?://[^"]+/upload/vod/[^"]+)"', html)
        if m:
            pic = m.group(1)
        return {
            "list": [{
                "vod_id": slug,
                "vod_name": name or slug,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": "线路1",
                "vod_play_url": f"正片${slug}",
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        pg = str(pg or "1")
        q = quote(key)
        if pg == "1":
            path = f"/vodsearch/?wd={q}"
        else:
            path = f"/vodsearch/page/{pg}/?wd={q}"
        html = self._get(path)
        videos = self._parse_list(html or "")
        pages = re.findall(r'aria-label="Go to page (\d+)"', html or "")
        pagecount = max((int(x) for x in pages if str(x).isdigit()), default=1)
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
        }

    def _extract_player_url(self, html):
        m = re.search(r"player_aaaa\s*=\s*(\{.+?\})\s*</script>", html or "", re.S)
        if m:
            try:
                return (json.loads(m.group(1)).get("url") or "").strip()
            except Exception:
                u = re.search(r'"url"\s*:\s*"([^"]+)"', m.group(1))
                if u:
                    return u.group(1).replace("\\/", "/")
        # embedUrl fallback
        m = re.search(r'"embedUrl"\s*:\s*"[^"]*\$(https?://[^"]+\.m3u8[^"]*)"', html or "")
        if m:
            return m.group(1)
        ms = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html or "")
        return ms[0] if ms else ""

    def _fetch_media(self, url):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    def _resolve_m3u8(self, url):
        if not url or ".m3u8" not in url:
            return url
        body = self._fetch_media(url)
        if not body:
            return url
        if "#EXTINF" in body and "#EXT-X-STREAM-INF" not in body:
            return url
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ".m3u8" in line:
                return line if line.startswith("http") else urljoin(url, line)
        return url

    def playerContent(self, flag, id, vipFlags=None):
        raw = id or ""
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/",
        }
        if raw.startswith("http"):
            url = self._resolve_m3u8(raw)
        else:
            html = self._get(f"/vodplay/{quote(raw)}")
            url = self._extract_player_url(html)
            if url:
                url = self._resolve_m3u8(url)
        if not url:
            return {"parse": 0, "jx": 0, "url": "", "header": header}
        if ".m3u8" in url and hasattr(self, "getProxyUrl") and self.getProxyUrl():
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
        return bool(re.search(r"\.(m3u8|mp4|flv|mkv)(\?|$)", url or "", re.I))

    def manualVideoCheck(self):
        return False

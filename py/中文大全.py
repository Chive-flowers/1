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

import re, time, json, hashlib, random, uuid, base64, urllib.request
from urllib.parse import quote, urljoin, urlparse, urlencode

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sympad
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False

class Spider(BaseSpider):
    def getName(self):
        return "中文大全"

    def init(self, extend=""):
        self.host = "https://kjwtwf.jvlookzw04.cn"
        self.api = "https://zdap.gkquu.cn:4438/zd"
        self.site = "kjwtwf.jvlookzw04.cn"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
        }
        self.guid = str(uuid.uuid4())
        self._logged = False
        self.aes_key = b"mh_aes=19@#$@%@#"
        self.aes_iv = b"5e1y6w452uqw9jq8"
        # plateId from getNewTabList
        self.plates = [
            ("4", "短视频"),
            ("22", "成人视频"),
            ("19", "成人天堂"),
            ("5", "长视频"),
            ("6", "AV"),
            ("7", "动漫"),
            ("8", "短剧合集"),
            ("day", "每日精选"),
        ]

    def getDependence(self):
        return "cryptography"

    def _nonce(self, n=16):
        chars = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
        return "".join(random.choice(chars) for _ in range(n))

    def _decrypt(self, s):
        if not HAS_CRYPTO or not s:
            return s
        try:
            raw = base64.b64decode(s)
            cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(self.aes_iv))
            dec = cipher.decryptor()
            pt = dec.update(raw) + dec.finalize()
            unpadder = sympad.PKCS7(128).unpadder()
            return (unpadder.update(pt) + unpadder.finalize()).decode("utf-8")
        except Exception:
            return s

    def _parse_rd(self, rd):
        if not isinstance(rd, str):
            return rd
        try:
            return json.loads(rd)
        except Exception:
            pass
        try:
            return json.loads(self._decrypt(rd))
        except Exception:
            return rd

    def _api(self, method, path, params=None, retry=4):
        params = params or {}
        last = None
        for attempt in range(retry):
            try:
                ts = str(int(time.time() * 1000))
                n = self._nonce()
                if method.upper() == "GET":
                    o = ts + self.guid + n + "@1243asd31**21#"
                    sign = hashlib.md5(o.encode()).hexdigest().upper()
                    headers = {
                        **self.headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "token": self.guid,
                        "sign": sign,
                        "timestamp": ts,
                        "nonce": n,
                        "url": self.site,
                        "Accept": "application/json",
                    }
                    url = self.api + path
                    if params:
                        url += "?" + urlencode(params)
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        return json.loads(resp.read().decode("utf-8", "ignore"))
                keys = sorted(params.keys())
                l = "&".join(f"{k}={params[k]}" for k in keys)
                o = l + ts + self.guid + n + "@1243asd31**21#"
                sign = hashlib.md5(o.encode()).hexdigest().upper()
                data = json.dumps({k: params[k] for k in keys}).encode()
                headers = {
                    **self.headers,
                    "Content-Type": "application/json",
                    "token": self.guid,
                    "sign": sign,
                    "timestamp": ts,
                    "nonce": n,
                    "url": self.site,
                }
                req = urllib.request.Request(self.api + path, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8", "ignore"))
            except Exception as e:
                last = e
                time.sleep(0.6 * (attempt + 1))
        if last:
            raise last
        return {}

    def _ensure_login(self):
        if self._logged:
            return
        for _ in range(3):
            try:
                r = self._api("POST", "/user/userLogin", {"deviceCode": self.guid, "platform": "pc"})
                if r.get("returnValue") == 1:
                    self._logged = True
                    return
            except Exception:
                time.sleep(0.8)
        self._logged = True  # continue anyway

    def homeContent(self, filter=None):
        classes = [{"type_id": p[0], "type_name": p[1]} for p in self.plates]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("day", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        self._ensure_login()
        pg = str(pg or "1")
        tid = str(tid)
        videos = []
        pagecount = int(pg)
        try:
            if tid == "day":
                r = self._api("GET", "/sp/getDayLovelyList", {"pageNo": pg, "pageSize": 24})
            else:
                r = self._api("GET", "/sp/getLabelVideoList", {
                    "plateId": tid, "labelId": 1, "pageNo": pg, "pageSize": 24
                })
            rd = self._parse_rd(r.get("returnData")) or {}
            vl = rd.get("videoList") or []
            total = int(rd.get("total") or 0)
            videos = self._map_videos(vl, tid)
            if total > 0:
                pagecount = max(1, (total + 23) // 24)
            elif len(videos) >= 20:
                pagecount = int(pg) + 1
        except Exception:
            videos = []
        return {
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
            "list": videos,
        }

    def _map_videos(self, vl, plate=""):
        out = []
        for v in vl:
            vid = str(v.get("videoId") or v.get("id") or "")
            if not vid:
                continue
            name = (v.get("videoTitle") or v.get("title") or vid).strip()
            pic = v.get("videoCover") or v.get("cover") or ""
            # embed play urls in id for faster player
            u1 = v.get("videoUrlOne") or ""
            u2 = v.get("videoUrlTwo") or ""
            u3 = v.get("videoUrlThree") or ""
            best = u2 or u1 or u3
            # vid|plate|url||title  (title after double pipe)
            vod_id = f"{vid}|{plate}|{best}||{name}"
            if len(vod_id) > 1200:
                vod_id = f"{vid}|{plate}|{best}"
            out.append({
                "vod_id": vod_id,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": v.get("videoTag") or "",
            })
        return out

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        raw = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        if "||" in raw:
            main, name = raw.split("||", 1)
        else:
            main, name = raw, ""
        parts = main.split("|")
        vid = parts[0]
        plate = parts[1] if len(parts) > 1 else ""
        embed = parts[2] if len(parts) > 2 else ""
        name = name or vid
        pic = ""
        play_url = f"正片${raw}"
        # try refresh detail if needed
        self._ensure_login()
        try:
            if plate and plate != "day":
                r = self._api("GET", "/sp/getVideoDetail", {"videoId": vid, "plateId": plate})
                rd = self._parse_rd(r.get("returnData")) or {}
                d = rd.get("videoDetail") or rd
                if isinstance(d, dict) and d:
                    name = d.get("videoTitle") or name
                    pic = d.get("videoCover") or pic
                    u2 = d.get("videoUrlTwo") or ""
                    u1 = d.get("videoUrlOne") or ""
                    u3 = d.get("videoUrlThree") or ""
                    best = u2 or u1 or u3 or embed
                    if best:
                        play_url = f"正片${vid}|{plate}|{best}"
        except Exception:
            pass
        return {
            "list": [{
                "vod_id": raw,
                "vod_name": name,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": "线路1",
                "vod_play_url": play_url,
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        self._ensure_login()
        pg = str(pg or "1")
        videos = []
        # try several plateIds
        for plate in ("6", "22", "5", "4"):
            try:
                r = self._api("GET", "/sp/getSearchList", {
                    "searchWord": key, "plateId": plate, "pageNo": pg, "pageSize": 24
                })
                rd = self._parse_rd(r.get("returnData")) or {}
                vl = rd.get("videoList") or []
                if vl:
                    videos = self._map_videos(vl, plate)
                    break
            except Exception:
                continue
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": 1 if len(videos) < 20 else int(pg) + 1,
            "limit": 24,
            "total": len(videos),
        }

    def _pick_url(self, raw):
        if raw.startswith("http"):
            return raw
        parts = str(raw).split("|")
        if len(parts) >= 3 and parts[2].startswith("http"):
            return parts[2]
        return ""

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
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/",
        }
        url = self._pick_url(id or "")
        if not url:
            # fallback: re-fetch list item not available; try detail
            parts = str(id or "").split("|")
            vid = parts[0] if parts else ""
            plate = parts[1] if len(parts) > 1 else "6"
            self._ensure_login()
            try:
                r = self._api("GET", "/sp/getVideoDetail", {"videoId": vid, "plateId": plate})
                rd = self._parse_rd(r.get("returnData")) or {}
                d = rd.get("videoDetail") or {}
                if isinstance(d, dict):
                    url = d.get("videoUrlTwo") or d.get("videoUrlOne") or d.get("videoUrlThree") or ""
            except Exception:
                url = ""
        if url:
            # prefer non-twitter mirror if twitter in url - already prefer two when mapping
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

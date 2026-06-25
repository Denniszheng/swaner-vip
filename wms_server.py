"""
Swaner Server — WMS Proxy + SKU Master API + Static Files
运行: python3 /opt/swaner/backend/wms_server.py
"""
import json, os, hashlib, time, hmac, sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs

PORT = 9601
WMS_APP_KEY = "60d2da562ee3492e8bdaaea44c611910"
WMS_SECRET = "e7f3e07d4f15438da02308fa1ebf90be"
WMS_BASE_URL = "https://api.xlwms.com"
DB_PATH = "/opt/swaner/data/sku_master.db"

# ── DB ──
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS sku_master (sku TEXT PRIMARY KEY, product_type TEXT, maintain_date TEXT, source TEXT, status TEXT)")
    conn.commit()
    conn.close()

# ── Sign ──
def make_sign(params, path, secret):
    sorted_keys = sorted(params.keys())
    def val_to_str(v):
        if isinstance(v, dict):
            return "{" + ",".join(f"{k}={iv}" for k, iv in v.items()) + "}"
        return str(v)
    step2 = "".join(f"{k}{val_to_str(params[k])}" for k in sorted_keys)
    return hmac.new(secret.encode(), (secret + path + step2 + secret).encode(), hashlib.sha256).hexdigest().upper()

# ── Product Type ──
def classify(sku, name):
    nl = (sku + " " + (name or "")).lower()
    for kw in ["stand","base","rotatable","display","holder","frame","mount","bracket","hook","hanger","pedestal","chain","22mm","connector","screw","spacer","ring","pin"]:
        if kw in nl: return "Accessories"
    return "Customization"

# ── Transform ──
def transform(wms_data):
    if wms_data.get("code") not in (200, "200"): return wms_data
    raw = wms_data.get("data", {})
    details = []
    for order in raw.get("orderList", []):
        for prod in order.get("productList", []):
            sku = prod.get("sku", "")
            details.append({
                "orderNo": order.get("outboundOrderNo", ""), "sku": sku,
                "qty": prod.get("quantity", 1),
                "productType": classify(sku, prod.get("productName", "")),
                "trackingNo": order.get("logisticsTrackNo", ""),
                "carrier": order.get("logisticsCarrier", ""),
                "productName": prod.get("productName", ""),
                "sheetUrl": order.get("sheetUrl", ""),
            })
    return {"code": 0, "data": {"waveNo": raw.get("waveNo", ""), "details": details}}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b"{}"

    # ── GET ──
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._json({"status":"ok","service":"swaner-server"})
        if u.path == "/sku/list":
            return self._sku_list(u)
        if u.path == "/sku/stats":
            return self._sku_stats()
        if u.path == "/image/list":
            return self._image_list(u)
        if u.path == "/image/overview":
            return self._image_overview()
        if u.path == "/image/remove":
            return self._image_remove(u)
        if u.path.startswith("/image/"):
            return self._serve_image(u.path.split("/image/")[1])
        self._json({"error":"not found"}, 404)

    # ── POST ──
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/wave-detail":
            return self._wave_detail()
        if u.path == "/sku/upsert":
            return self._sku_upsert()
        if u.path == "/sku/delete":
            return self._sku_delete()
        if u.path == "/image/upload":
            return self._image_upload()
        self._json({"error":"not found"}, 404)

    # ── Wave Detail ──
    def _wave_detail(self):
        try:
            body = json.loads(self._read_body())
            wave_no = body.get("waveNo", "")
            if not wave_no: return self._json({"error":"missing waveNo"}, 400)
            ts = str(int(time.time()))
            path = "/openapi/v2/wave/detail"
            params = {"appKey": WMS_APP_KEY, "data": {"waveNo": wave_no}, "timestamp": ts}
            sign = make_sign(params, path, WMS_SECRET)
            rb = json.dumps({"appKey":WMS_APP_KEY,"data":{"waveNo":wave_no},"timestamp":ts,"sign":sign})
            req = Request(f"{WMS_BASE_URL}{path}", method="POST", data=rb.encode())
            req.add_header("Content-Type", "application/json")
            req.add_header("Accept", "application/json")
            resp = urlopen(req, timeout=30)
            self._json(transform(json.loads(resp.read())))
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── SKU List ──
    def _sku_list(self, u):
        try:
            qp = parse_qs(u.query)
            conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
            where = []; params = []
            if qp.get("productType"):
                where.append("product_type=?"); params.append(qp["productType"][0])
            if qp.get("dateFrom"):
                where.append("maintain_date>=?"); params.append(qp["dateFrom"][0])
            if qp.get("dateTo"):
                where.append("maintain_date<=?"); params.append(qp["dateTo"][0])
            if qp.get("search"):
                where.append("sku LIKE ?"); params.append(f"%{qp['search'][0]}%")
            sql = "SELECT * FROM sku_master" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY maintain_date DESC"
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            data = [{"sku":r["sku"],"productType":r["product_type"],"maintainDate":r["maintain_date"],"source":r["source"],"status":r["status"]} for r in rows]
            self._json({"data": data, "total": len(data)})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── SKU Stats ──
    def _sku_stats(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT product_type, maintain_date FROM sku_master").fetchall()
            conn.close()
            stats = {"total": len(rows), "standard":0, "accessories":0, "customization":0, "byDate":{}}
            for pt, md in rows:
                if pt == "Standard": stats["standard"] += 1
                elif pt == "Accessories": stats["accessories"] += 1
                else: stats["customization"] += 1
                if md: stats["byDate"][md] = stats["byDate"].get(md, 0) + 1
            self._json({"data": stats})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── SKU Upsert ──
    def _sku_upsert(self):
        try:
            body = json.loads(self._read_body())
            items = body.get("skus", [])
            if not items: return self._json({"error":"missing skus"}, 400)
            today = time.strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            for it in items:
                conn.execute("INSERT OR REPLACE INTO sku_master(sku,product_type,maintain_date,source,status) VALUES(?,?,?,?,?)",
                    (it["sku"], it.get("productType","Customization"), it.get("maintainDate",today), it.get("source","manual"), it.get("status","")))
            conn.commit(); conn.close()
            self._json({"ok": True, "count": len(items)})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── SKU Delete ──
    def _sku_delete(self):
        try:
            body = json.loads(self._read_body())
            skus = body.get("skus", [])
            if not skus: return self._json({"error":"missing skus"}, 400)
            conn = sqlite3.connect(DB_PATH)
            conn.execute(f"DELETE FROM sku_master WHERE sku IN ({','.join(['?']*len(skus))})", skus)
            deleted = conn.total_changes; conn.commit(); conn.close()
            self._json({"ok": True, "deleted": deleted})
        except Exception as e:
            self._json({"error": str(e)}, 500)

# Image Upload
    def _image_upload(self):
        try:
            body = json.loads(self._read_body())
            images = body.get("images", [])
            if not images and body.get("sku"):
                images = [body]
            if not images:
                return self._json({"error":"missing images array"}, 400)
            img_dir = "/opt/swaner/data/images"
            os.makedirs(img_dir, exist_ok=True)
            today = time.strftime("%Y-%m-%d")
            import base64 as b64
            results = []
            for img in images:
                sku = (img.get("sku") or "").strip()
                base64_data = img.get("image","")
                product_type = img.get("productType","Customization")
                if not sku:
                    results.append({"sku":"","ok":False,"error":"SKU name empty"}); continue
                if not base64_data:
                    results.append({"sku":sku,"ok":False,"error":"image data empty"}); continue
                try:
                    ext = img.get("fileType","png").lower()
                    if ext not in ("png","jpg","jpeg","gif","webp"): ext = "png"
                    filepath = os.path.join(img_dir, f"{sku}.{ext}")
                    with open(filepath, "wb") as f:
                        f.write(b64.b64decode(base64_data))
                    fsize = os.path.getsize(filepath)
                    if fsize < 100:
                        os.remove(filepath)
                        results.append({"sku":sku,"ok":False,"error":"file too small (%d bytes)" % fsize}); continue
                    conn = sqlite3.connect(DB_PATH)
                    exists = conn.execute("SELECT 1 FROM sku_master WHERE sku=?", (sku,)).fetchone()
                    if not exists:
                        conn.execute("INSERT INTO sku_master(sku,product_type,maintain_date,source,status) VALUES(?,?,?,?,?)",
                            (sku, product_type, today, "manual", ""))
                    conn.commit(); conn.close()
                    results.append({"sku":sku,"ok":True,"size":fsize})
                except Exception as e:
                    results.append({"sku":sku,"ok":False,"error":str(e)[:100]})
            ok_count = sum(1 for r in results if r.get("ok"))
            self._json({"ok":ok_count>0,"total":len(results),"ok_count":ok_count,"fail_count":len(results)-ok_count,"results":results})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # Image List (read all SKUs from disk, type from .type file)
    def _image_list(self, u):
        try:
            img_dir = "/opt/swaner/data/images"
            os.makedirs(img_dir, exist_ok=True)
            qp = parse_qs(u.query)
            filter_pt = qp.get("productType", [None])[0]
            filter_search = (qp.get("search", [""])[0] or "").lower()
            IMG_EXTS = {".png",".jpg",".jpeg",".gif",".webp"}
            all_files = os.listdir(img_dir)
            # Collect SKU info from both image files and .type files
            by_sku = {}
            for f in all_files:
                fpath = os.path.join(img_dir, f)
                if not os.path.isfile(fpath): continue
                ext = os.path.splitext(f)[1].lower()
                sku = f.rsplit(".",1)[0]
                stat = os.stat(fpath)
                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                if ext == ".type":
                    try:
                        with open(fpath) as tf: ptype = tf.read().strip()
                    except: ptype = "Product"
                    if sku not in by_sku:
                        by_sku[sku] = {"sku": sku, "fileName": "", "size": 0,
                            "productType": ptype, "mtime": mtime, "maintainDate": ""}
                    else:
                        by_sku[sku]["productType"] = ptype
                elif ext in IMG_EXTS:
                    if sku in by_sku:
                        by_sku[sku].update({"fileName": f, "size": stat.st_size, "mtime": mtime,
                            "maintainDate": time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))})
                    else:
                        by_sku[sku] = {"sku": sku, "fileName": f, "size": stat.st_size,
                            "productType": "Product", "mtime": mtime,
                            "maintainDate": time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))}
            result = [e for e in by_sku.values()
                      if (not filter_pt or e.get("productType") == filter_pt)
                      and (not filter_search or filter_search in e["sku"].lower())]
            self._json({"data": result, "total": len(result)})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # Image Overview (reads type from .type files)
    def _image_overview(self):
        try:
            img_dir = "/opt/swaner/data/images"
            os.makedirs(img_dir, exist_ok=True)
            IMG_EXTS = {".png",".jpg",".jpeg",".gif",".webp"}
            sku_info = {}  # {sku: {productType, maintainDate}}
            img_skus = set()
            for f in os.listdir(img_dir):
                fpath = os.path.join(img_dir, f)
                if not os.path.isfile(fpath): continue
                parts = f.rsplit(".",1)
                sku = parts[0]
                ext = "." + parts[1] if len(parts) > 1 else ""
                if ext == ".type":
                    try:
                        with open(fpath) as tf:
                            ptype = tf.read().strip()
                        if sku not in sku_info: sku_info[sku] = {"productType": "Product", "maintainDate": ""}
                        sku_info[sku]["productType"] = ptype
                    except: pass
                elif ext.lower() in IMG_EXTS:
                    img_skus.add(sku)
                    mtime = time.strftime("%Y-%m-%d", time.localtime(os.stat(fpath).st_mtime))
                    if sku not in sku_info: sku_info[sku] = {"productType": "Product", "maintainDate": ""}
                    sku_info[sku]["maintainDate"] = mtime
                elif sku not in sku_info:
                    sku_info[sku] = {"productType": "Product", "maintainDate": ""}
            has_img = []
            missing_img = []
            for sku, info in sku_info.items():
                entry = {"sku": sku, "productType": info["productType"], "maintainDate": info.get("maintainDate","")}
                if sku in img_skus: has_img.append(entry)
                else: missing_img.append(entry)
            self._json({"data": {"total_skus": len(sku_info), "has_image": len(has_img),
                "missing_image": len(missing_img), "missing_list": missing_img, "has_list": has_img}})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # Image Delete (GET method, takes skus from query param to bypass Cloudflare WAF)
    def _image_remove(self, u):
        try:
            qp = parse_qs(u.query)
            skus_raw = qp.get("skus", [""])[0]
            skus = [s.strip() for s in skus_raw.split(",") if s.strip()]
            if not skus: return self._json({"error":"missing skus"}, 400)
            img_dir = "/opt/swaner/data/images"
            deleted = 0
            for sku in skus:
                for ext in ("png","jpg","jpeg","gif","webp","type"):
                    fpath = os.path.join(img_dir, f"{sku}.{ext}")
                    if os.path.exists(fpath):
                        os.remove(fpath); deleted += 1
            self._json({"ok": True, "deleted": deleted})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # Serve Image (handles both /image/SKU and /image/SKU.png)
    def _serve_image(self, sku):
        try:
            img_dir = "/opt/swaner/data/images"
            # 1) Try exact match first (for URLs with extension, e.g. /image/ABC.png)
            fpath = os.path.join(img_dir, sku)
            if os.path.isfile(fpath):
                ext = sku.rsplit(".",1)[-1].lower()
                ctype = "image/" + ("jpeg" if ext=="jpg" else ext)
                with open(fpath, "rb") as f: data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "max-age=86400")
                self._cors(); self.end_headers()
                self.wfile.write(data); return
            # 2) Try appending common extensions (for URLs without extension, e.g. /image/ABC)
            base = sku
            for ext in ("png","jpg","jpeg","gif","webp"):
                fpath = os.path.join(img_dir, f"{base}.{ext}")
                if os.path.isfile(fpath):
                    with open(fpath, "rb") as f: data = f.read()
                    ctype = "image/" + ("jpeg" if ext=="jpg" else ext)
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "max-age=86400")
                    self._cors(); self.end_headers()
                    self.wfile.write(data); return
            self._json({"error":"image not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0] if args else ''}")

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0] if args else ''}")


if __name__ == "__main__":
    init_db()
    print(f"Swaner Server starting on 0.0.0.0:{PORT}")
    print(f"  DB: {DB_PATH}")
    print(f"  WMS: {WMS_BASE_URL}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

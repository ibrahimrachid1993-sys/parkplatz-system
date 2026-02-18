 import os, json, re, uuid, csv, requests
from datetime import datetime
from io import BytesIO, StringIO
from flask import Flask, request, jsonify, make_response, render_template_string
from PIL import Image, ImageEnhance

app = Flask(__name__, static_folder="static", static_url_path="/static")

ROWS, COLS = 4, 4
AREAS = ROWS * COLS
MAX_CAPACITY = 650

DATA_FILE = "data.json"
HISTORY_FILE = "history.json"

OCR_API_KEY = "K86896712788957"
OCR_API_URL = "https://api.ocr.space/parse/image"

VIN_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
VIN_FIND  = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
LAGER_FIND = re.compile(r"\b[A-Z]{2}\d{5}\b")  # LK12345

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

zones = load_json(DATA_FILE, {str(i): [] for i in range(AREAS)})
history = load_json(HISTORY_FILE, [])

def ensure_zone_keys():
    for i in range(AREAS):
        zones.setdefault(str(i), [])

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except:
        return None

def validate_vin(v):
    v = (v or "").upper().strip()
    return v if VIN_REGEX.match(v) else None

def validate_lager(l):
    l = (l or "").upper().strip()
    l = re.sub(r"[\s\-]", "", l)
    m = re.search(r"([A-Z]{2}\d{5})", l)
    return m.group(1) if m else None

def preprocess_image(img: Image.Image) -> Image.Image:
    if img.mode != "L":
        img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Brightness(img).enhance(1.15)
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    return img

def total_cars():
    return sum(len(v) for v in zones.values())

def ocr_space_extract(file_storage):
    try:
        img = Image.open(file_storage.stream)
        img = preprocess_image(img)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)

        r = requests.post(
            OCR_API_URL,
            files={"file": ("scan.jpg", buf, "image/jpeg")},
            data={"apikey": OCR_API_KEY, "language": "eng", "OCREngine": 2},
            timeout=40
        )
        data = r.json()
        if not data.get("ParsedResults"):
            return None, None, "Kein Text erkannt"

        text = (data["ParsedResults"][0].get("ParsedText", "") or "").upper()

        vin = None
        lager = None
        m_v = VIN_FIND.search(text)
        if m_v:
            vin = validate_vin(m_v.group(0))
        m_l = LAGER_FIND.search(text)
        if m_l:
            lager = validate_lager(m_l.group(0))

        if not vin and not lager:
            return None, None, "VIN/Lager nicht gefunden"
        return vin, lager, None
    except Exception as e:
        return None, None, f"OCR Fehler: {str(e)}"

def find_car_by_id(car_id):
    for zid, cars in zones.items():
        for c in cars:
            if c.get("id") == car_id:
                return zid, c
    return None, None

def history_add(vin, lager, zone_num, in_time):
    history.append({
        "hid": uuid.uuid4().hex,
        "vin": vin,
        "lager": lager,
        "zone": zone_num,
        "in_time": in_time,
        "out_time": ""
    })

def history_close(vin, out_time):
    for h in reversed(history):
        if h.get("vin") == vin and (h.get("out_time") or "") == "":
            h["out_time"] = out_time
            return True
    return False

def csv_response(filename, rows):
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    for r in rows:
        writer.writerow(r)
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    return resp

def filter_history_by_date(field, from_date, to_date):
    f = datetime.strptime(from_date, "%Y-%m-%d")
    t = datetime.strptime(to_date, "%Y-%m-%d")
    t_end = datetime(t.year, t.month, t.day, 23, 59)

    res = []
    for h in history:
        ts = (h.get(field) or "").strip()
        if not ts:
            continue
        dt = parse_dt(ts)
        if not dt:
            continue
        if f <= dt <= t_end:
            res.append(h)
    return res

HTML_PAGE = """
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parkplatz</title>
<style>
:root{--bg:#f4f6fb;--card:#fff;--stroke:rgba(0,0,0,.1);--r:18px}
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;background:var(--bg);margin:0;padding:14px}
.card{background:var(--card);border:1px solid var(--stroke);border-radius:var(--r);padding:14px;margin-bottom:12px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
h2{margin:0 0 10px}
img{width:100%;border-radius:14px;border:1px solid var(--stroke)}
button{padding:12px 14px;border-radius:14px;border:0;background:#2563eb;color:#fff;font-weight:800}
input{padding:12px;border-radius:12px;border:1px solid var(--stroke);width:100%}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.small{color:#666;font-size:13px}
.zone{border:1px solid var(--stroke);border-radius:14px;padding:12px;margin-top:10px;background:rgba(0,0,0,.02)}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
</style>
</head>
<body>

<div class="card">
  <h2>🚗 Parkplatz</h2>
  <div class="small">Belegt: <b>{{belegt}}</b> • Frei: <b>{{frei}}</b> • Kapazität: <b>{{maxcap}}</b></div>
</div>

<div class="card">
  <h2>🗺️ Karte</h2>
  <div class="small">Wenn du hier ein Bild siehst, ist /static korrekt.</div>
  <img src="/static/parking_lot.png" alt="parking">
</div>

<div class="card">
  <h2>📸 OCR Test (Foto → Scan)</h2>
  <div class="row">
    <input id="photo" type="file" accept="image/*" capture="environment">
    <button onclick="scan()">Scannen</button>
  </div>
  <div id="scanres" class="small" style="margin-top:10px;"></div>
</div>

<div class="card">
  <h2>⬇️ CSV Export</h2>
  <div class="row">
    <a href="/api/export_current_csv"><button>Alle aktuellen</button></a>
  </div>
  <div class="small" style="margin-top:10px;">
    History Export kommt wieder rein sobald Deploy stabil läuft.
  </div>
</div>

<div class="card">
  <h2>🚙 Aktuelle Fahrzeuge</h2>
  {% for zi, cars in zones.items() %}
    <div class="zone">
      <b>Zone {{zi|int+1}}</b> <span class="small">({{cars|length}})</span>
      {% for c in cars %}
        <div class="small">VIN <b class="mono">{{c.vin}}</b> • Lager <b class="mono">{{c.lager}}</b> • Ein <b>{{c.in_time}}</b></div>
      {% endfor %}
      {% if cars|length == 0 %}
        <div class="small">leer</div>
      {% endif %}
    </div>
  {% endfor %}
</div>

<script>
async function scan(){
  const f = document.getElementById("photo").files[0];
  if(!f){ alert("Bitte Foto wählen"); return; }
  const fd = new FormData();
  fd.append("image", f);
  const r = await fetch("/api/ocr_scan", {method:"POST", body: fd});
  const j = await r.json();
  document.getElementById("scanres").textContent = JSON.stringify(j);
}
</script>

</body>
</html>
"""

@app.route("/")
def index():
    ensure_zone_keys()
    belegt = total_cars()
    frei = MAX_CAPACITY - belegt
    return render_template_string(
        HTML_PAGE,
        zones=zones,
        belegt=belegt,
        frei=frei,
        maxcap=MAX_CAPACITY
    )

@app.route("/api/ocr_scan", methods=["POST"])
def api_ocr_scan():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "Kein Bild erhalten"}), 400
    vin, lager, err = ocr_space_extract(request.files["image"])
    if err:
        return jsonify({"success": False, "error": err}), 400
    return jsonify({"success": True, "vin": vin or "", "lager": lager or ""})

@app.route("/api/export_current_csv")
def export_current_csv():
    rows = [["VIN","Lagernummer","Zone","Einlagerung"]]
    for zid, cars in zones.items():
        for c in cars:
            rows.append([c.get("vin",""), c.get("lager",""), str(int(zid)+1), c.get("in_time","")])
    return csv_response(f"parkplatz_aktuell_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", rows)

if __name__ == "__main__":
    # Render nutzt PORT env var
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
 

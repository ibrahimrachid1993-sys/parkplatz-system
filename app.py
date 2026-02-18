 import os, json, re, uuid, csv, requests
from datetime import datetime
from io import BytesIO, StringIO

from flask import Flask, request, jsonify, make_response, render_template_string
from PIL import Image, ImageEnhance

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ===================== SETTINGS =====================
ROWS = 4
COLS = 4
AREAS = ROWS * COLS              # 16
MAX_CAPACITY = 650               # Gesamt-Kapazität (alle Zonen zusammen)

DATA_FILE = "data.json"
HISTORY_FILE = "history.json"

# OCR.space
OCR_API_KEY = "K86896712788957"
OCR_API_URL = "https://api.ocr.space/parse/image"

VIN_REGEX = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
VIN_FIND  = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
LAGER_FIND = re.compile(r"\b[A-Z]{2}\d{5}\b")  # z.B. LK12345
# ===================================================

# ===================== STORAGE ======================
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
history = load_json(HISTORY_FILE, [])  # list of {hid, vin, lager, zone, in_time, out_time}
# ===================================================

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
    # OCR boost (schnell & robust)
    if img.mode != "L":
        img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Brightness(img).enhance(1.15)
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    return img

def total_cars():
    return sum(len(v) for v in zones.values())

def ensure_zone_keys():
    # falls Datei kaputt oder Keys fehlen
    for i in range(AREAS):
        zones.setdefault(str(i), [])

ensure_zone_keys()

# ===================== API HELPERS ==================
def ocr_space_extract(file_storage):
    """
    Nimmt ein Bild (multipart upload) und extrahiert VIN + Lager
    """
    try:
        img = Image.open(file_storage.stream)
        img = preprocess_image(img)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)

        r = requests.post(
            OCR_API_URL,
            files={"file": ("scan.jpg", buf, "image/jpeg")},
            data={
                "apikey": OCR_API_KEY,
                "language": "eng",
                "OCREngine": 2
            },
            timeout=40
        )
        data = r.json()

        if not data.get("ParsedResults"):
            return None, None, "Kein Text erkannt (OCR)"

        text = data["ParsedResults"][0].get("ParsedText", "") or ""
        text = text.upper()

        vin = None
        lager = None

        m_v = VIN_FIND.search(text)
        if m_v:
            vv = validate_vin(m_v.group(0))
            if vv:
                vin = vv

        m_l = LAGER_FIND.search(text)
        if m_l:
            ll = validate_lager(m_l.group(0))
            if ll:
                lager = ll

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
        "zone": zone_num,       # 1-16
        "in_time": in_time,
        "out_time": ""
    })

def history_close(vin, out_time):
    # erster offener Eintrag wird geschlossen
    for h in reversed(history):
        if h.get("vin") == vin and (h.get("out_time") or "") == "":
            h["out_time"] = out_time
            return True
    return False
# ====================================================

# ===================== ROUTES =======================
@app.route("/")
def index():
    ensure_zone_keys()
    belegt = total_cars()
    frei = MAX_CAPACITY - belegt
    return render_template_string(
        HTML_PAGE,
        rows=ROWS, cols=COLS, areas=AREAS,
        zones=zones,
        belegt=belegt,
        frei=frei,
        maxcap=MAX_CAPACITY
    )

@app.route("/api/ocr_scan", methods=["POST"])
def api_ocr_scan():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "Kein Bild erhalten"}), 400
    f = request.files["image"]
    vin, lager, err = ocr_space_extract(f)
    if err:
        return jsonify({"success": False, "error": err}), 400
    return jsonify({"success": True, "vin": vin or "", "lager": lager or ""})

@app.route("/api/add_car", methods=["POST"])
def api_add_car():
    data = request.get_json(force=True, silent=True) or {}
    zone = str(data.get("zone", "")).strip()
    vin = validate_vin(data.get("vin"))
    lager = validate_lager(data.get("lager"))
    ready_date = data.get("ready_date") or None
    ready_time = data.get("ready_time") or None
    notes = (data.get("notes") or "").strip()

    if zone == "" or not zone.isdigit() or int(zone) < 0 or int(zone) >= AREAS:
        return jsonify({"success": False, "error": "Zone ungültig"}), 400
    if not vin:
        return jsonify({"success": False, "error": "VIN ungültig (17 Zeichen)"}), 400
    if not lager:
        return jsonify({"success": False, "error": "Lagernummer ungültig (LK12345)"}), 400

    # Duplikate (VIN schon vorhanden)
    for zid, cars in zones.items():
        for c in cars:
            if c.get("vin") == vin:
                return jsonify({"success": False, "error": "Diese VIN ist schon eingeparkt"}), 400

    t = now_str()
    car = {
        "id": uuid.uuid4().hex,
        "vin": vin,
        "lager": lager,
        "in_time": t,
        "ready_date": ready_date,
        "ready_time": ready_time,
        "notes": notes
    }

    zones.setdefault(zone, [])
    zones[zone].append(car)

    history_add(vin, lager, int(zone) + 1, t)

    save_json(DATA_FILE, zones)
    save_json(HISTORY_FILE, history)

    return jsonify({"success": True})

@app.route("/api/remove_car", methods=["POST"])
def api_remove_car():
    data = request.get_json(force=True, silent=True) or {}
    car_id = data.get("car_id")
    if not car_id:
        return jsonify({"success": False, "error": "car_id fehlt"}), 400

    zid, car = find_car_by_id(car_id)
    if not car:
        return jsonify({"success": False, "error": "Auto nicht gefunden"}), 404

    zones[zid] = [c for c in zones[zid] if c.get("id") != car_id]

    out_t = now_str()
    history_close(car.get("vin"), out_t)

    save_json(DATA_FILE, zones)
    save_json(HISTORY_FILE, history)

    return jsonify({"success": True})

@app.route("/api/move_car", methods=["POST"])
def api_move_car():
    data = request.get_json(force=True, silent=True) or {}
    car_id = data.get("car_id")
    to_zone = str(data.get("to_zone", "")).strip()

    if not car_id:
        return jsonify({"success": False, "error": "car_id fehlt"}), 400
    if to_zone == "" or not to_zone.isdigit() or int(to_zone) < 0 or int(to_zone) >= AREAS:
        return jsonify({"success": False, "error": "Ziel-Zone ungültig"}), 400

    from_zid, car = find_car_by_id(car_id)
    if not car:
        return jsonify({"success": False, "error": "Auto nicht gefunden"}), 404

    # entfernen
    zones[from_zid] = [c for c in zones[from_zid] if c.get("id") != car_id]
    # hinzufügen
    zones.setdefault(to_zone, [])
    zones[to_zone].append(car)

    save_json(DATA_FILE, zones)
    return jsonify({"success": True})

@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").upper().strip()
    if not q:
        return jsonify([])

    # 1) aktuelle
    for zid, cars in zones.items():
        for c in cars:
            if q in c.get("vin","") or q in (c.get("lager","") or ""):
                out = dict(c)
                out["zone"] = int(zid) + 1
                out["out_time"] = ""
                out["source"] = "current"
                return jsonify([out])

    # 2) history
    for h in reversed(history):
        if q in (h.get("vin","") or "") or q in (h.get("lager","") or ""):
            out = dict(h)
            out["source"] = "history"
            return jsonify([out])

    return jsonify([])

@app.route("/api/history")
def api_history():
    q = (request.args.get("q") or "").upper().strip()
    items = list(reversed(history))
    if q:
        items = [h for h in items if q in (h.get("vin","") or "") or q in (h.get("lager","") or "")]
    return jsonify(items[:200])

# ---------------- CSV EXPORTS ----------------
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

@app.route("/api/export_current_csv")
def export_current_csv():
    rows = []
    rows.append(["VIN","Lagernummer","Zone","Einlagerung","Bereitstellung Datum","Bereitstellung Uhrzeit","Notiz"])
    for zid, cars in zones.items():
        for c in cars:
            rows.append([
                c.get("vin",""),
                c.get("lager",""),
                str(int(zid)+1),
                c.get("in_time",""),
                c.get("ready_date","") or "",
                c.get("ready_time","") or "",
                c.get("notes","") or ""
            ])
    return csv_response(f"parkplatz_aktuell_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", rows)

def filter_history_by_date(field, from_date, to_date):
    # field = "in_time" oder "out_time"
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

@app.route("/api/export_history_out_csv")
def export_history_out_csv():
    f = request.args.get("from")
    t = request.args.get("to")
    if not f or not t:
        return jsonify({"success": False, "error": "from/to fehlt"}), 400

    items = filter_history_by_date("out_time", f, t)
    rows = [["VIN","Lagernummer","Zone","Einlagerung","Auslagerung"]]
    for h in items:
        rows.append([h.get("vin",""), h.get("lager",""), str(h.get("zone","")), h.get("in_time",""), h.get("out_time","")])
    return csv_response(f"history_auslagerung_{f}_bis_{t}.csv", rows)

@app.route("/api/export_history_in_csv")
def export_history_in_csv():
    f = request.args.get("from")
    t = request.args.get("to")
    if not f or not t:
        return jsonify({"success": False, "error": "from/to fehlt"}), 400

    items = filter_history_by_date("in_time", f, t)
    rows = [["VIN","Lagernummer","Zone","Einlagerung","Auslagerung"]]
    for h in items:
        rows.append([h.get("vin",""), h.get("lager",""), str(h.get("zone","")), h.get("in_time",""), h.get("out_time","")])
    return csv_response(f"history_einlagerung_{f}_bis_{t}.csv", rows)

# =====================================================
# TEIL 2 kommt unten als HTML_PAGE
# =====================================================
HTML_PAGE = """
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parkplatz</title>

<style>
body{
    margin:0;
    font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;
    background:#f2f4f8;
}
.wrap{max-width:980px;margin:auto;padding:14px}
.card{
    background:white;
    border-radius:18px;
    padding:14px;
    margin-bottom:14px;
    box-shadow:0 10px 25px rgba(0,0,0,.08)
}
h2{margin:0 0 10px}
button{
    border:0;
    border-radius:14px;
    padding:12px 14px;
    font-weight:800;
    cursor:pointer
}
.btn{background:#2563eb;color:white}
.btn2{background:#111827;color:white}
.btnGhost{background:#e5edff;color:#2563eb}
input,select{
    width:100%;
    padding:12px;
    border-radius:14px;
    border:1px solid #d0d5dd;
    font-size:15px
}
.small{font-size:13px;color:#555}
canvas{
    width:100%;
    border-radius:16px;
    border:1px solid #d0d5dd;
    display:block
}
.zone{
    border:1px solid #ddd;
    border-radius:14px;
    padding:10px;
    margin-top:10px
}
.car{
    background:#f8fafc;
    border-radius:12px;
    padding:10px;
    margin-top:8px
}
.mono{font-family:monospace;font-weight:700}
.modal{
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.6);
    display:none;
    align-items:center;
    justify-content:center;
    z-index:1000
}
.modalBox{
    background:white;
    border-radius:20px;
    padding:14px;
    width:95%;
    max-width:500px
}
</style>
</head>

<body>
<div class="wrap">

<div class="card">
<h2>🅿️ Parkplatz Übersicht</h2>
<div class="small">
Belegt: <b>{{belegt}}</b> |
Frei: <b>{{frei}}</b> |
Kapazität: <b>{{maxcap}}</b>
</div>
</div>

<div class="card">
<h2>🗺️ Karte</h2>
<canvas id="map"></canvas>

<div style="display:flex;gap:8px;margin-top:10px">
<input id="search" placeholder="VIN oder Lagernummer">
<button class="btnGhost" onclick="search()">Suchen</button>
<button class="btn" onclick="openAdd()">+ Auto</button>
</div>

<div id="searchResult" class="small"></div>
</div>

<div class="card">
<h2>📦 CSV Export</h2>

<button class="btn" onclick="dl('/api/export_current_csv')">
Alle aktuellen Fahrzeuge
</button>

<hr>

<div class="small">Auslagerung Zeitraum</div>
<input type="date" id="outFrom">
<input type="date" id="outTo">
<button class="btnGhost" onclick="dlOut()">Export Auslagerung</button>

<hr>

<div class="small">Einlagerung Zeitraum</div>
<input type="date" id="inFrom">
<input type="date" id="inTo">
<button class="btnGhost" onclick="dlIn()">Export Einlagerung</button>
</div>

<div class="card">
<h2>🚗 Zonen</h2>

{% for zid,cars in zones.items() %}
<div class="zone">
<b>Zone {{zid|int+1}}</b>

{% if cars|length == 0 %}
<div class="small">leer</div>
{% endif %}

{% for c in cars %}
<div class="car">
<div class="mono">{{c.vin}}</div>
<div class="small">
Lager: {{c.lager}}<br>
Einlagerung: {{c.in_time}}
</div>

<button class="btnGhost" onclick="openMove('{{c.id}}')">↔️ Move</button>
<button class="btn2" onclick="removeCar('{{c.id}}')">🚚 Ausparken</button>
</div>
{% endfor %}
</div>
{% endfor %}
</div>

</div>

<!-- ADD MODAL -->
<div class="modal" id="addModal">
<div class="modalBox">
<h2>➕ Auto hinzufügen</h2>

<select id="zone">
<option value="">Zone wählen</option>
{% for i in range(areas) %}
<option value="{{i}}">Zone {{i+1}}</option>
{% endfor %}
</select>

<input id="vin" placeholder="VIN (17 Zeichen)">
<input id="lager" placeholder="Lagernummer (LK12345)">

<input type="file" id="photo" accept="image/*" capture="environment">
<button class="btnGhost" onclick="scan()">📷 Scannen</button>

<input type="date" id="readyDate">
<input type="time" id="readyTime">
<input id="notes" placeholder="Notiz">

<button class="btn" onclick="save()">Speichern</button>
<button class="btnGhost" onclick="closeAdd()">Abbrechen</button>
</div>
</div>

<!-- MOVE MODAL -->
<div class="modal" id="moveModal">
<div class="modalBox">
<h2>↔️ Verschieben</h2>
<input type="hidden" id="moveId">
<select id="moveTo">
{% for i in range(areas) %}
<option value="{{i}}">Zone {{i+1}}</option>
{% endfor %}
</select>
<button class="btn" onclick="doMove()">Verschieben</button>
<button class="btnGhost" onclick="closeMove()">Abbrechen</button>
</div>
</div>

<script>
const rows={{rows}}, cols={{cols}};
const zones={{zones|tojson}};
let highlight=null;

const cv=document.getElementById("map");
const ctx=cv.getContext("2d");
const img=new Image();
img.src="/static/parking_lot.png";

function resize(){
cv.width=cv.clientWidth;
cv.height=cv.clientWidth;
draw();
}
img.onload=resize;
window.onresize=resize;

function draw(){
ctx.clearRect(0,0,cv.width,cv.height);
ctx.drawImage(img,0,0,cv.width,cv.height);

let w=cv.width/cols,h=cv.height/rows;
for(let r=0;r<rows;r++){
for(let c=0;c<cols;c++){
let i=r*cols+c;
ctx.strokeStyle="#000";
ctx.strokeRect(c*w,r*h,w,h);
if(zones[i]&&zones[i].length){
ctx.fillStyle="rgba(37,99,235,.25)";
ctx.fillRect(c*w,r*h,w,h);
ctx.fillStyle="#fff";
ctx.font="bold 18px Arial";
ctx.textAlign="center";
ctx.textBaseline="middle";
ctx.fillText(zones[i].length,c*w+w/2,r*h+h/2);
}
if(highlight===i){
ctx.strokeStyle="yellow";
ctx.lineWidth=5;
ctx.strokeRect(c*w,r*h,w,h);
}
}
}
}

cv.onclick=e=>{
const r=cv.getBoundingClientRect();
const x=e.clientX-r.left;
const y=e.clientY-r.top;
const c=Math.floor(x/(cv.width/cols));
const rr=Math.floor(y/(cv.height/rows));
openAdd(rr*cols+c);
};

function openAdd(z=null){
if(z!==null)document.getElementById("zone").value=z;
document.getElementById("addModal").style.display="flex";
}
function closeAdd(){document.getElementById("addModal").style.display="none";}
function openMove(id){
document.getElementById("moveId").value=id;
document.getElementById("moveModal").style.display="flex";
}
function closeMove(){document.getElementById("moveModal").style.display="none";}

async function scan(){
const f=document.getElementById("photo").files[0];
if(!f){alert("Kein Bild");return;}
let fd=new FormData();
fd.append("image",f);
let r=await fetch("/api/ocr_scan",{method:"POST",body:fd});
let j=await r.json();
if(!j.success){alert(j.error);return;}
if(j.vin)vin.value=j.vin;
if(j.lager)lager.value=j.lager;
}

async function save(){
let r=await fetch("/api/add_car",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({
zone:zone.value,
vin:vin.value,
lager:lager.value,
ready_date:readyDate.value||null,
ready_time:readyTime.value||null,
notes:notes.value||""
})});
let j=await r.json();
if(!j.success){alert(j.error);return;}
location.reload();
}

async function removeCar(id){
if(!confirm("Wirklich ausparken?"))return;
await fetch("/api/remove_car",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({car_id:id})});
location.reload();
}

async function doMove(){
await fetch("/api/move_car",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({car_id:moveId.value,to_zone:moveTo.value})});
location.reload();
}

async function search(){
let q=document.getElementById("search").value;
if(!q)return;
let r=await fetch("/api/search?q="+encodeURIComponent(q));
let j=await r.json();
if(!j.length){searchResult.innerText="Nicht gefunden";highlight=null;draw();return;}
highlight=j[0].zone-1;
searchResult.innerText="Gefunden in Zone "+j[0].zone;
draw();
}

function dl(u){window.location=u;}
function dlOut(){
dl(`/api/export_history_out_csv?from=${outFrom.value}&to=${outTo.value}`);
}
function dlIn(){
dl(`/api/export_history_in_csv?from=${inFrom.value}&to=${inTo.value}`);
}
</script>

</body>
</html>
"""
# ===================== START =======================
if __name__ == "__main__":
    # Beim Start: fehlende Keys ergänzen + speichern (damit nichts crasht)
    ensure_zone_keys()
    try:
        save_json(DATA_FILE, zones)
        save_json(HISTORY_FILE, history)
    except:
        pass

    # Wichtig: damit /static/parking_lot.png sauber ausgeliefert wird
    # (Flask nimmt dafür automatisch den Ordner "static")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

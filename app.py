import os, json, uuid, csv, re
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, send_file

app = Flask(__name__)

DATA_FILE = "data.json"
ROWS = 4
COLS = 4
MAX_CAPACITY = 650

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "zones": {str(i): [] for i in range(ROWS*COLS)},
        "history": []
    }

def save_data(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

data = load_data()

def total_cars():
    return sum(len(data["zones"][z]) for z in data["zones"])

def valid_vin(vin):
    return re.match(r"^[A-Z]{2}[0-9]{5}$", vin)

@app.route("/")
def index():
    return render_template_string(PAGE,
        rows=ROWS,
        cols=COLS,
        zones=data["zones"],
        history=data["history"],
        total=total_cars(),
        free=MAX_CAPACITY-total_cars()
    )

@app.route("/add/<zone>", methods=["POST"])
def add(zone):
    vin = request.form["vin"].upper()
    lager = request.form["lager"]

    if not valid_vin(vin):
        return "VIN falsch! Format: 2 Buchstaben + 5 Zahlen (z.B. LK12345)"

    car = {
        "id": uuid.uuid4().hex,
        "vin": vin,
        "lager": lager,
        "in_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "out_time": ""
    }

    data["zones"][zone].append(car)
    save_data(data)
    return redirect("/")

@app.route("/remove/<zone>/<car_id>")
def remove(zone, car_id):
    for car in data["zones"][zone]:
        if car["id"] == car_id:
            car["out_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["history"].append(car)
            data["zones"][zone].remove(car)
            break
    save_data(data)
    return redirect("/")

@app.route("/export_all")
def export_all():
    filename = "export_all.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["VIN","Lager","Einlagerung","Auslagerung"])
        for z in data["zones"]:
            for c in data["zones"][z]:
                writer.writerow([c["vin"],c["lager"],c["in_time"],c["out_time"]])
        for c in data["history"]:
            writer.writerow([c["vin"],c["lager"],c["in_time"],c["out_time"]])
    return send_file(filename, as_attachment=True)

@app.route("/export_range", methods=["POST"])
def export_range():
    start = request.form["start"]
    end = request.form["end"]
    filename = "export_range.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["VIN","Lager","Einlagerung","Auslagerung"])
        for c in data["history"]:
            if start <= c["out_time"][:10] <= end:
                writer.writerow([c["vin"],c["lager"],c["in_time"],c["out_time"]])
    return send_file(filename, as_attachment=True)

PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Arial;background:#f3f6fb;margin:15px}
h2{text-align:center}
.card{background:white;padding:15px;border-radius:18px;margin-bottom:15px;box-shadow:0 8px 20px rgba(0,0,0,0.1)}
canvas{width:100%;max-width:800px;border-radius:20px;box-shadow:0 15px 40px rgba(0,0,0,0.2)}
button{padding:10px;border:none;border-radius:12px;background:#111;color:white;margin-top:5px}
input{padding:8px;border-radius:8px;border:1px solid #ddd;margin:4px 0;width:100%}
details{margin-bottom:10px}
.zonebox{margin-top:10px;padding:10px;border-radius:12px;background:#f9fafc}
</style>
</head>
<body>

<h2>Parkplatz System</h2>

<div class="card">
Gesamt: {{total}} | Frei: {{free}}
</div>

<canvas id="map"></canvas>

<div class="card">
<a href="/export_all"><button>CSV Export Alles</button></a>
</div>

<div class="card">
<form action="/export_range" method="post">
Von: <input type="date" name="start" required>
Bis: <input type="date" name="end" required>
<button>Export Zeitraum</button>
</form>
</div>

<details class="card">
<summary>Zonen</summary>

{% for z in zones %}
<div class="zonebox">
<b>Zone {{z}}</b>

<form method="POST" action="/add/{{z}}">
<input name="vin" placeholder="VIN (z.B. LK12345)" required>
<input name="lager" placeholder="Lagernummer" required>
<button>Auto hinzufügen</button>
</form>

{% for c in zones[z] %}
<div>
VIN: {{c.vin}} | Lager: {{c.lager}} | {{c.in_time}}
<a href="/remove/{{z}}/{{c.id}}">
<button>Auslagern</button>
</a>
</div>
{% endfor %}
</div>
{% endfor %}
</details>

<details class="card">
<summary>History</summary>
{% for c in history %}
<div>
VIN: {{c.vin}} | Lager: {{c.lager}} | Rein: {{c.in_time}} | Raus: {{c.out_time}}
</div>
{% endfor %}
</details>

<script>
const rows={{rows}}, cols={{cols}};
const canvas=document.getElementById("map");
const ctx=canvas.getContext("2d");
const img=new Image();
img.src="/static/parking_lot.png";

img.onload=()=>{
canvas.width=img.width;
canvas.height=img.height;
ctx.drawImage(img,0,0);
let w=canvas.width/cols,h=canvas.height/rows;
for(let r=0;r<rows;r++){
 for(let c=0;c<cols;c++){
  ctx.strokeRect(c*w,r*h,w,h);
 }
}
};
</script>

</body>
</html>
"""

if __name__ == "__main__":
    app.run()

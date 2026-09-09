from __future__ import annotations
from pathlib import Path
import json, os, shutil, urllib.request, urllib.error, urllib.parse
import duckdb
from fastapi import FastAPI, Query, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATIC = ROOT / "static"
TMP = Path("/tmp/nexo")
TMP.mkdir(parents=True, exist_ok=True)
POSTES_TMP = TMP / "Postes_Electricos_Nacional_GeoLibre.parquet"

FILES = {
    "mufas": ["Mufas TIGO.geojson", "Mufas_TIGO.geojson", "Mufas TIGO.json"],
    "oc": ["OC_TIGO.geojson", "OC TIGO.geojson"],
    "fen": ["Equipos FEN por OC.geojson", "Equipos_FEN_por_OC.geojson"],
}

app = FastAPI(title="NEXO AI v3.9 Render")

def find_file(kind: str):
    for name in FILES[kind]:
        p = DATA / name
        if p.exists(): return p
    for p in DATA.glob("*"):
        low = p.name.lower()
        if kind=="mufas" and "mufa" in low and p.suffix.lower() in (".geojson",".json"): return p
        if kind=="oc" and low.startswith("oc") and p.suffix.lower() in (".geojson",".json"): return p
        if kind=="fen" and "fen" in low and p.suffix.lower() in (".geojson",".json"): return p
    return None

def geo_count(path):
    if not path: return 0
    try:
        return len(json.loads(path.read_text(encoding="utf-8")).get("features", []))
    except Exception:
        return 0

def validate_parquet(path: Path):
    if not path.exists() or path.stat().st_size < 12:
        raise ValueError("Archivo Parquet vacío o inválido")
    with path.open("rb") as f:
        if f.read(4) != b"PAR1":
            raise ValueError("Firma inicial PAR1 inválida")
        f.seek(-4, 2)
        if f.read(4) != b"PAR1":
            raise ValueError("Firma final PAR1 inválida")

def parquet_count(path: Path):
    con = duckdb.connect()
    try:
        return int(con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
    finally:
        con.close()

@app.get("/health")
def health():
    return {"ok": True, "service": "NEXO AI v3.9"}

@app.get("/api/status")
def status():
    m,o,f = find_file("mufas"),find_file("oc"),find_file("fen")
    p_ready = POSTES_TMP.exists()
    p_count = 0
    if p_ready:
        try: p_count = parquet_count(POSTES_TMP)
        except Exception: p_ready = False
    return {
        "mufas":{"ready":bool(m),"name":m.name if m else "","count":geo_count(m)},
        "oc":{"ready":bool(o),"name":o.name if o else "","count":geo_count(o)},
        "fen":{"ready":bool(f),"name":f.name if f else "","count":geo_count(f)},
        "postes":{"ready":p_ready,"name":POSTES_TMP.name if p_ready else "","count":p_count},
        "hosting":"Render Free",
        "postes_mode":"temporary"
    }

@app.get("/api/base/{kind}")
def base(kind: str):
    if kind not in FILES:
        return JSONResponse({"error":"base inválida"}, status_code=404)
    p=find_file(kind)
    if not p:
        return JSONResponse({"error":f"{kind} no cargado en repositorio"}, status_code=404)
    return FileResponse(p, media_type="application/geo+json")

@app.post("/api/postes/upload")
async def upload_postes(file: UploadFile = File(...)):
    name=(file.filename or "").lower()
    if not (name.endswith(".parquet") or name.endswith(".pq")):
        return JSONResponse({"error":"Solo se admite GeoParquet .parquet/.pq"}, status_code=400)
    TMP.mkdir(parents=True, exist_ok=True)
    temp = POSTES_TMP.with_suffix(".uploading")
    total_written=0
    try:
        with temp.open("wb") as dst:
            while True:
                chunk=await file.read(1024*1024)  # 1 MB, evita cargar 210 MB en RAM
                if not chunk: break
                dst.write(chunk); total_written += len(chunk)
        os.replace(temp, POSTES_TMP)
        validate_parquet(POSTES_TMP)
        count=parquet_count(POSTES_TMP)
        return {"ok":True,"name":file.filename,"bytes":total_written,"count":count}
    except Exception as e:
        temp.unlink(missing_ok=True)
        POSTES_TMP.unlink(missing_ok=True)
        return JSONResponse({"error":str(e)}, status_code=400)
    finally:
        await file.close()

@app.delete("/api/postes/session")
def delete_postes():
    POSTES_TMP.unlink(missing_ok=True)
    return {"ok":True}

@app.get("/api/postes")
def postes(
    min_lon: float=Query(...), min_lat: float=Query(...),
    max_lon: float=Query(...), max_lat: float=Query(...),
    limit: int=Query(1200, ge=1, le=1200)
):
    if not POSTES_TMP.exists():
        return JSONResponse({"error":"Carga primero el GeoParquet de postes para esta sesión"}, status_code=404)
    con=duckdb.connect()
    try:
        total=con.execute("""
            SELECT count(*) FROM read_parquet(?)
            WHERE longitud BETWEEN ? AND ? AND latitud BETWEEN ? AND ?
        """,[str(POSTES_TMP),min_lon,max_lon,min_lat,max_lat]).fetchone()[0]
        cols=["empresa_id","poste_id","empresa","material","altura",
              "apoyo_comunicaciones","tipo_propiedad","longitud","latitud"]
        rows=con.execute(f"""
            SELECT {",".join(cols)}
            FROM read_parquet(?)
            WHERE longitud BETWEEN ? AND ? AND latitud BETWEEN ? AND ?
            LIMIT ?
        """,[str(POSTES_TMP),min_lon,max_lon,min_lat,max_lat,limit]).fetchall()
        return {"total":int(total),"rows":[dict(zip(cols,r)) for r in rows]}
    finally:
        con.close()


@app.get("/api/ai/status")
def ai_status():
    key=os.getenv("OPENAI_API_KEY","").strip()
    model=os.getenv("OPENAI_MODEL","gpt-5.6-luna").strip()
    return {"configured":bool(key),"model":model if key else "","mode":"server_api" if key else "local_only"}

def _extract_response_text(data:dict)->str:
    if isinstance(data.get("output_text"),str) and data["output_text"].strip():
        return data["output_text"].strip()
    texts=[]
    for item in data.get("output",[]) or []:
        for c in item.get("content",[]) or []:
            if isinstance(c.get("text"),str):texts.append(c["text"])
    return "\n".join(texts).strip()

@app.post("/api/ai/chat")
async def ai_chat(request:Request):
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:return JSONResponse({"error":"OPENAI_API_KEY no configurada"},status_code=503)
    body=await request.json();question=str(body.get("question","")).strip();context=body.get("context") or {}
    if not question:return JSONResponse({"error":"Pregunta vacía"},status_code=400)
    model=os.getenv("OPENAI_MODEL","gpt-5.6-luna").strip()
    payload={"model":model,"input":[
      {"role":"system","content":[{"type":"input_text","text":
       "Eres NEXO AI, asistente técnico de ingeniería de fibra óptica. Responde en español, breve y operacional. No inventes datos técnicos."}]},
      {"role":"user","content":[{"type":"input_text","text":
       f"CONTEXTO NEXO:\n{json.dumps(context,ensure_ascii=False)}\n\nPREGUNTA:\n{question}"}]}
    ],"max_output_tokens":700}
    req=urllib.request.Request("https://api.openai.com/v1/responses",
      data=json.dumps(payload).encode("utf-8"),
      headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=60) as resp:data=json.loads(resp.read().decode("utf-8"))
        text=_extract_response_text(data)
        return {"answer":text,"model":model} if text else JSONResponse({"error":"Sin texto"},status_code=502)
    except urllib.error.HTTPError as e:
        return JSONResponse({"error":"OpenAI API error","detail":e.read().decode("utf-8","ignore")[:1000]},status_code=502)
    except Exception as e:return JSONResponse({"error":str(e)},status_code=502)



@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=3)):
    """
    Convierte una dirección chilena a coordenadas.
    Ej.: Avenida Apoquindo 3000, Las Condes, Santiago
    """
    query = q.strip()
    params = urllib.parse.urlencode({
        "q": query,
        "format": "jsonv2",
        "limit": 5,
        "countrycodes": "cl",
        "addressdetails": 1
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NEXO-AI-FO/3.9 (fiber feasibility geocoder)",
            "Accept-Language": "es-CL,es;q=0.9"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        results = []
        for r in rows:
            results.append({
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "display_name": r.get("display_name", query),
                "type": r.get("type"),
                "category": r.get("category"),
                "address": r.get("address", {})
            })
        return {"query": query, "results": results}
    except Exception as e:
        return JSONResponse({"error": f"No fue posible geocodificar la dirección: {e}"}, status_code=502)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

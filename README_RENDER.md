# NEXO AI v3.4 — Render Free

## Arquitectura
- Render Free: FastAPI + DuckDB.
- Mufas, OC y FEN/MEN: guardados en `data/` dentro del repositorio.
- Postes nacional GeoParquet: NO se guarda en GitHub ni Render.
- El usuario lo carga manualmente por sesión.
- El backend lo guarda temporalmente en `/tmp/nexo/`.
- Render Free usa filesystem efímero: al reiniciar, redeployar o dormir, el GeoParquet desaparece.

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`uvicorn app.server:app --host 0.0.0.0 --port $PORT`

Health Check:
`/health`

Compute:
`Free`

## GitHub
Subir:
- app/
- static/
- data/ (solo las 3 bases ligeras)
- requirements.txt
- render.yaml
- .python-version

NO subir el GeoParquet nacional de postes.

## Postes
Desde NEXO:
1. Seleccionar `.parquet`.
2. Cargar GeoParquet temporal.
3. Abrir mapa.
4. NEXO consulta DuckDB por BBOX.
5. Al terminar, usar “Descargar postes de la sesión”, o dejar que Render los elimine cuando la instancia se reinicie/detenga.

#!/usr/bin/env python3
"""
actualizar_local.py - Regenera los JSON del dashboard CLRM03 desde este equipo y los publica en GitHub.

Reemplaza a push_to_github.py (que referenciaba scripts inexistentes). Pensado para correr a mano o
desde una tarea programada mientras el workflow de GitHub Actions no tenga una service account valida.

Uso:
  python actualizar_local.py              # regenera y publica
  python actualizar_local.py --no-push    # solo regenera (prueba)
  python actualizar_local.py --force-sinmov   # fuerza sin_movimiento aunque tenga <20 h

Requisitos: credenciales de gcloud (gcloud auth application-default login) y sesion de gh/git para el push.
"""
import os, subprocess, sys, time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
# Carpeta con google-cloud-bigquery y dependencias (opcional). Se puede definir con la variable CLRM03_LIBS;
# si no existe, se usan las librerias del Python que ejecuta el script.
LIBS = os.environ.get("CLRM03_LIBS") or os.path.join(os.path.expanduser("~"), "clrm03-ocupacion-py", "libs")
SINMOV_FILE = os.path.join(DATA, "sin_movimiento_CLRM03.json")
SINMOV_MAX_AGE_H = 20   # la consulta escanea ~37 GB (~US$0.17): una vez al dia alcanza

STEPS = [
    ["generar_checkin_json.py", "--dias", "3"],
    ["generar_putaway_json.py"],
    ["fetch_ocupacion.py"],
    ["generar_mz_json.py"],
]

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore")
if os.path.isdir(LIBS):
    env["PYTHONPATH"] = LIBS + os.pathsep + env.get("PYTHONPATH", "")

def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=BASE, env=env, capture_output=True, text=True, **kw)

def regenerate(step):
    t = time.time()
    r = run([sys.executable, *step])
    ok = r.returncode == 0
    log(f"{'OK ' if ok else 'ERROR'} {' '.join(step)} ({time.time()-t:.0f}s)")
    if not ok:
        log("  " + (r.stderr or r.stdout).strip().splitlines()[-1][:300] if (r.stderr or r.stdout).strip() else "  sin detalle")
    return ok

def sinmov_is_old():
    if not os.path.exists(SINMOV_FILE):
        return True
    return (time.time() - os.path.getmtime(SINMOV_FILE)) / 3600 > SINMOV_MAX_AGE_H

CUT_SNIPPET = (
    "import warnings; warnings.filterwarnings('ignore')\n"
    "from google.cloud import bigquery\n"
    "c = bigquery.Client(project='meli-bi-data')\n"
    "r = list(c.query(\"SELECT FORMAT_DATETIME('%Y-%m-%d %H:%M:%S', MAX(FBM_LAST_UPDATED)) AS cut "
    "FROM `meli-bi-data.WHOWNER.BT_FBM_STOCK_3_ADDRESS` WHERE WAREHOUSE_ID = 'CLRM03'\").result())\n"
    "print('CUT=' + str(r[0].cut))\n"
)

def bq_has_new_data():
    """True si BigQuery tiene stock mas reciente que el ultimo publicado (consulta de ~0.06 GB).
    Si no se puede comprobar, devuelve True: ante la duda se actualiza."""
    try:
        import json
        with open(os.path.join(DATA, "ocupacion_CLRM03.json"), encoding="utf-8") as f:
            local_cut = json.load(f).get("stock_cut")
        r = run([sys.executable, "-c", CUT_SNIPPET])
        out = [l for l in r.stdout.splitlines() if l.startswith("CUT=")]
        if r.returncode != 0 or not out or not local_cut:
            return True
        remote_cut = out[0][4:].strip()
        log(f"corte de stock: BigQuery {remote_cut} | publicado {local_cut}")
        return remote_cut != local_cut
    except Exception as e:
        log(f"no se pudo comparar cortes ({e}); se actualiza igual")
        return True

def main():
    no_push = "--no-push" in sys.argv
    if "--force" not in sys.argv and not bq_has_new_data():
        log("BigQuery sin datos nuevos desde la ultima publicacion: no se hace nada")
        return 0
    steps = list(STEPS)
    if "--force-sinmov" in sys.argv or sinmov_is_old():
        steps.append(["fetch_sin_movimiento.py"])
    else:
        log("sin_movimiento reciente (<%dh): se omite" % SINMOV_MAX_AGE_H)

    results = {" ".join(s): regenerate(s) for s in steps}
    failed = [k for k, v in results.items() if not v]
    if failed:
        log("Fallaron: " + ", ".join(failed) + " -> no se publica nada")
        return 1
    if no_push:
        log("--no-push: listo, sin publicar")
        return 0

    git = ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    run(["git", "add", "data/"])
    if run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        log("Sin cambios que publicar")
        return 0
    msg = f"data: actualizacion local {datetime.now():%Y-%m-%d %H:%M}"
    c = run(["git", "-c", "user.name=Francisca Rojas", "-c", "user.email=francisca.rojasvallejos@mercadolibre.cl",
             "commit", "-m", msg])
    if c.returncode != 0:
        log("ERROR commit: " + (c.stderr or c.stdout).strip()[-300:])
        return 1
    p = run([*git, "pull", "--rebase", "-X", "theirs", "origin", "main"])
    if p.returncode != 0:
        log("ERROR pull --rebase: " + (p.stderr or p.stdout).strip()[-300:])
        return 1
    pu = run([*git, "push", "origin", "main"])
    if pu.returncode != 0:
        log("ERROR push: " + (pu.stderr or pu.stdout).strip()[-300:])
        return 1
    log("Publicado en GitHub: " + msg)
    return 0

if __name__ == "__main__":
    sys.exit(main())

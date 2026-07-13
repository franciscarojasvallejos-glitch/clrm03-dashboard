#!/usr/bin/env python3
"""
watch_dashboard.py
Actualiza el dashboard CLRM03 cada hora mientras esté corriendo.
Ejecutar: python watch_dashboard.py
"""
import subprocess, time, sys, os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
INTERVAL = 5 * 60  # 5 minutos en segundos

def run(script):
    print(f"  > {script}", flush=True)
    result = subprocess.run([sys.executable, os.path.join(BASE, script)], cwd=BASE)
    return result.returncode == 0

def push():
    cmds = [
        ["git", "add", "data/checkin_CLRM03.json", "data/putaway_CLRM03.json"],
        ["git", "commit", "-m", f"Auto-update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, cwd=BASE)

def ciclo():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"\n[{now}] Actualizando...", flush=True)
    run("generar_checkin_json.py")
    run("generar_putaway_json.py")
    push()
    print(f"[{datetime.now().strftime('%H:%M')}] OK. Proxima actualizacion en {INTERVAL//60} min.", flush=True)
    print(f"Dashboard CLRM03 — actualizacion automatica cada {INTERVAL//60} min.", flush=True)

if __name__ == "__main__":
    print(f"Dashboard CLRM03 — actualización automática cada {INTERVAL//60} min.")
    print("Presiona Ctrl+C para detener.\n")
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
        time.sleep(INTERVAL)

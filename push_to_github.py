# -*- coding: utf-8 -*-
"""
push_to_github.py — Regenera los JSON de datos y los sube a GitHub Pages.
Ejecutar cada 60 min (o manualmente con doble click en ACTUALIZAR.bat).
"""
import subprocess, sys, os

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

scripts = [
    "generar_checkin_json.py",
    "generar_volumetria_json.py",
    "generar_catalog_json.py",
    "fetch_ocupacion.py",
    "fetch_responsables.py",
    "fetch_disponibles.py",
]

def run(script):
    path = os.path.join(REPO_DIR, script)
    if not os.path.exists(path):
        print(f"  [skip] {script} no encontrado")
        return
    print(f"  [run] {script}")
    result = subprocess.run([sys.executable, path], capture_output=True, text=True, cwd=REPO_DIR)
    if result.returncode != 0:
        print(f"  [error] {script}: {result.stderr[:200]}")
    else:
        print(f"  [ok] {script}")

def git(cmd):
    result = subprocess.run(["git"] + cmd, capture_output=True, text=True, cwd=REPO_DIR)
    if result.stdout.strip():
        print(" ", result.stdout.strip())
    return result.returncode

print("=== Generando datos ===")
for s in scripts:
    run(s)

print("\n=== Subiendo a GitHub ===")
git(["add", "data/"])
git(["commit", "-m", "datos actualizados"])
code = git(["push", "origin", "main"])

if code == 0:
    print("\n✓ Datos publicados en GitHub Pages")
    print("  https://franciscarojasvallejos-glitch.github.io/clrm03-dashboard/")
else:
    print("\n[!] Push falló — revisa credenciales de GitHub")

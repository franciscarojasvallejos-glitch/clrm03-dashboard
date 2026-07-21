#!/usr/bin/env python3
"""
generar_mz_json.py
Genera:
  data/mz_heatmap_CLRM03.json    — bays agregados (tablero principal)
  data/mz_slots_MZ0.json, ...    — slots con SKU detalle por zona (para panel)

Uso:
  python generar_mz_json.py              # una vez
  python generar_mz_json.py --watch 300  # cada 5 min (default)
"""
import csv, json, os, sys, io, time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE    = os.path.dirname(os.path.abspath(__file__))
CSV_IN  = os.path.join(BASE, 'CLRM03_MZ_Ubicaciones.csv')
DATA_DIR = os.path.join(BASE, 'data')
JSON_BAYS = os.path.join(DATA_DIR, 'mz_heatmap_CLRM03.json')
MAX_SKUS = 6

ZONE_ORDER = ['MZ0 - Piso 1','MZ1 - Piso 2','MZ2 - Piso 3',
              'MZ3 - Piso 4','RS - Rack Selectivo','HV - High Value']

def generate():
    now = datetime.now(tz=ZoneInfo('America/Santiago'))
    print(f"[{now.strftime('%H:%M:%S')}] Procesando {CSV_IN}...", flush=True)

    bays   = defaultdict(lambda: defaultdict(lambda: defaultdict(
                lambda: {'slots':0,'stock':0,'skus_sum':0,'skus_max':0})))
    slots_by_zone = defaultdict(lambda: defaultdict(list))  # zona -> "p-b" -> [slot]
    zstats = defaultdict(lambda: {'total_slots':0,'total_stock':0})

    with open(CSV_IN, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            zona    = row['ZONA']
            p       = row['PASILLO']
            b       = row['BAY']
            nv      = row['NIVEL']
            ps      = row['POSICION']
            n_skus  = int(row['N_SKUS'])
            stock   = int(row['STOCK_TOTAL'])
            detalle = row['DETALLE_SKUS']

            bay_key = f"{p}-{b}"
            slots_by_zone[zona][bay_key].append({
                'id': row['UBICACION'],
                'nv': nv, 'ps': ps,
                'sk': n_skus,
                'st': stock,
                'd':  detalle,
            })

            bk = bays[zona][p][b]
            bk['slots']    += 1
            bk['stock']    += stock
            bk['skus_sum'] += n_skus
            bk['skus_max']  = max(bk['skus_max'], n_skus)

            zstats[zona]['total_slots'] += 1
            zstats[zona]['total_stock'] += stock

    # ── JSON bays (tablero) ────────────────────────────────────────────────────
    zones_out = {}
    for zona in ZONE_ORDER:
        if zona not in bays: continue
        zone_bays = []
        aisles_set, bays_set = set(), set()
        for p, bay_dict in bays[zona].items():
            for b, bk in bay_dict.items():
                aisles_set.add(p); bays_set.add(b)
                avg = round(bk['skus_sum'] / bk['slots'], 2)
                zone_bays.append({
                    'p': p, 'b': b,
                    'n': bk['slots'], 'st': bk['stock'],
                    'avg': avg, 'mx': bk['skus_max'],
                    'pct': round(avg / MAX_SKUS * 100, 1),
                })
        zone_bays.sort(key=lambda x: (x['p'], x['b']))
        zs = zstats[zona]
        zones_out[zona] = {
            'label':       zona,
            'aisles':      sorted(aisles_set),
            'bays_range':  sorted(bays_set),
            'total_slots': zs['total_slots'],
            'total_stock': zs['total_stock'],
            'data':        zone_bays,
        }

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {'updated': now.strftime('%Y-%m-%d %H:%M'), 'warehouse': 'CLRM03',
           'max_skus': MAX_SKUS, 'zones': zones_out}
    with open(JSON_BAYS, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',',':'), ensure_ascii=False)
    kb = os.path.getsize(JSON_BAYS) / 1024
    print(f"  Bays JSON: {JSON_BAYS} ({kb:.0f} KB)", flush=True)

    # ── JSON slots por zona ────────────────────────────────────────────────────
    for zona, bay_dict in slots_by_zone.items():
        safe = zona.replace(' ','_').replace('/','_').replace('-','').replace('(','').replace(')','')
        path = os.path.join(DATA_DIR, f'mz_slots_{safe}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'zona': zona, 'updated': now.strftime('%H:%M'), 'bays': bay_dict},
                      f, separators=(',',':'), ensure_ascii=False)
        kb = os.path.getsize(path) / 1024
        n_slots = sum(len(v) for v in bay_dict.values())
        print(f"  Slots {zona}: {n_slots:,} slots → {kb:.0f} KB", flush=True)

    print(f"  Listo. Proxima actualizacion en breve.", flush=True)

def main():
    args     = sys.argv[1:]
    watch    = '--watch' in args
    interval = 300
    if watch:
        idx = args.index('--watch')
        if idx + 1 < len(args):
            try: interval = int(args[idx+1])
            except ValueError: pass
        print(f"Modo watch — actualizando cada {interval}s. Ctrl+C para detener.", flush=True)
        while True:
            try:
                generate()
            except Exception as e:
                print(f"  ERROR: {e}", flush=True)
            time.sleep(interval)
    else:
        generate()

if __name__ == '__main__':
    main()

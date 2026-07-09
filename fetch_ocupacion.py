#!/usr/bin/env python3
"""
fetch_ocupacion.py — CLRM03 Ocupación de Ubicaciones RK/BL
Genera data/ocupacion_CLRM03.json

Uso:
  python fetch_ocupacion.py          # genera el snapshot
  python fetch_ocupacion.py --watch  # refresca cada 60s
"""
import json, os, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
import warnings
warnings.filterwarnings('ignore')
from google.cloud import bigquery

PROJECT   = 'meli-bi-data'
WAREHOUSE = 'CLRM03'
TZ        = ZoneInfo('America/Santiago')
CAP_BY_ZONE = {'RK': 6, 'BL': 1}

def parse_addr(addr):
    if not addr: return None
    p = addr.split('-')
    if len(p) < 6: return None
    try:
        return (p[0], int(p[2]), int(p[3]), int(p[4]), int(p[5]))
    except ValueError:
        return None

def fetch():
    client = bigquery.Client(project=PROJECT)

    # Paso 1: todas las ubicaciones físicas disponibles en WMS (incluye vacías)
    q_layout = f"""
    SELECT
      ADDRESS_ID,
      CAST(ADRS_AISLE    AS INT64) AS aisle,
      CAST(ADRS_BAY      AS INT64) AS bay,
      CAST(ADRS_LEVEL    AS INT64) AS level,
      CAST(ADRS_POSITION AS INT64) AS pos,
      COALESCE(ADRS_STORAGE_TYPE,    '')  AS adrs_type,
      COALESCE(ADRS_ADDRESS_TYPE_ID, '')  AS adrs_class
    FROM `{PROJECT}.WHOWNER.LK_FBM_WMS_ADDRESSES`
    WHERE WAREHOUSE_ID = '{WAREHOUSE}'
      AND (UPPER(ADDRESS_ID) LIKE 'RK%' OR UPPER(ADDRESS_ID) LIKE 'BL%')
      AND LOWER(ADRS_STATUS) = 'available'
    """
    print("  Consultando layout WMS...", end=' ', flush=True)
    layout_rows = list(client.query(q_layout).result())
    print(f"{len(layout_rows):,} ubicaciones físicas")

    slots = {}
    for r in layout_rows:
        aid = r.ADDRESS_ID
        parsed = parse_addr(aid)
        zone = parsed[0] if parsed else aid.split('-')[0]
        tipo = (r.adrs_type or '').strip()
        clase = (r.adrs_class or '').strip()
        slots[aid] = {
            'id': aid, 'zone': zone,
            'aisle': r.aisle, 'bay': r.bay, 'level': r.level, 'pos': r.pos,
            'cap': CAP_BY_ZONE.get(zone, 6),
            'tipo': tipo, 'clase': clase,
            'qty': 0, 'avail': 0, 'res': 0, 'skus': []
        }

    # Paso 2: stock actual
    q_stock = f"""
    SELECT ADDRESS_ID, INVENTORY_ID,
      FBM_QUANTITY AS qty, FBM_AVAILABLE AS avail,
      FBM_RESERVED AS res
    FROM `{PROJECT}.WHOWNER.BT_FBM_STOCK_3_ADDRESS`
    WHERE WAREHOUSE_ID = '{WAREHOUSE}'
      AND (UPPER(ADDRESS_ID) LIKE 'RK%' OR UPPER(ADDRESS_ID) LIKE 'BL%')
      AND FBM_STOCK_STATUS = 'ok'
    ORDER BY ADDRESS_ID, INVENTORY_ID
    """
    print("  Consultando stock...", end=' ', flush=True)
    stock_rows = list(client.query(q_stock).result())
    print(f"{len(stock_rows):,} registros con stock")

    for r in stock_rows:
        aid = r.ADDRESS_ID
        if aid not in slots:
            parsed = parse_addr(aid)
            if not parsed: continue
            zone = parsed[0]
            slots[aid] = {
                'id': aid, 'zone': zone,
                'aisle': parsed[1], 'bay': parsed[2], 'level': parsed[3], 'pos': parsed[4],
                'cap': CAP_BY_ZONE.get(zone, 6),
                'qty': 0, 'avail': 0, 'res': 0, 'skus': []
            }
        s = slots[aid]
        s['qty']   += int(r.qty or 0)
        s['avail'] += int(r.avail or 0)
        s['res']   += int(r.res or 0)
        s['skus'].append(r.INVENTORY_ID)

    bays = {}
    for s in slots.values():
        key = (s['zone'], s['aisle'], s['bay'])
        if key not in bays:
            bays[key] = {
                'zone': s['zone'], 'aisle': s['aisle'], 'bay': s['bay'],
                'n_slots': 0, 'n_cap': 0, 'n_skus': 0, 'qty': 0, 'avail': 0,
                'slots': []
            }
        b = bays[key]
        b['n_slots'] += 1
        b['n_cap']   += s['cap']
        b['n_skus']  += len(s['skus'])
        b['qty']     += s['qty']
        b['avail']   += s['avail']
        b['slots'].append({'id': s['id'], 'level': s['level'], 'pos': s['pos'],
                           'tipo': s.get('tipo',''), 'clase': s.get('clase',''),
                           'skus': s['skus'], 'qty': s['qty'], 'avail': s['avail']})

    bays_list = sorted(bays.values(), key=lambda b: (b['aisle'], b['bay']))
    total_slots     = len(slots)
    all_skus        = [len(s['skus']) for s in slots.values()]
    total_sku_slots = sum(all_skus)
    # Ocupación = slots con al menos 1 SKU / total slots (no dividir por cap)
    locs_occ        = sum(1 for s in slots.values() if len(s['skus']) > 0)
    # Multi-SKU = slots con más de 1 SKU diferente
    locs_multi      = sum(1 for s in slots.values() if len(s['skus']) > 1)
    stats = {
        'total_slots': total_slots, 'total_bays': len(bays),
        'total_sku_slots': total_sku_slots,
        'locs_occ': locs_occ,
        'locs_multi': locs_multi,
        # % de ubicaciones físicas que tienen al menos 1 SKU
        'pct_occ': round(locs_occ / total_slots * 100, 1) if total_slots else 0,
        # % de ubicaciones con más de 1 SKU (multi-SKU)
        'pct_multi': round(locs_multi / locs_occ * 100, 1) if locs_occ else 0,
        'avg_skus': round(sum(all_skus)/len(all_skus), 2) if all_skus else 0,
        'max_skus': max(all_skus) if all_skus else 0,
        'locs_full': sum(1 for s in slots.values() if len(s['skus']) >= s['cap']),
        'locs_1': sum(1 for s in slots.values() if len(s['skus'])==1),
        'locs_2': sum(1 for s in slots.values() if len(s['skus'])==2),
        'locs_3': sum(1 for s in slots.values() if len(s['skus'])==3),
        'locs_4plus': sum(1 for s in slots.values() if len(s['skus'])>=4),
    }
    now = datetime.now(tz=TZ)
    return {'date': now.strftime('%Y-%m-%d'), 'updated': now.strftime('%H:%M:%S'),
            'wh': WAREHOUSE, 'stats': stats, 'bays': bays_list}

def save(data):
    os.makedirs('data', exist_ok=True)
    path = 'data/ocupacion_CLRM03.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
    kb = os.path.getsize(path) / 1024
    s = data['stats']
    print(f"  Guardado: {path}  ({s['total_slots']:,} slots · {s['total_bays']:,} bays · {kb:.0f} KB)")

def main():
    args = sys.argv[1:]
    watch = '--watch' in args
    interval = 60
    if watch:
        idx = args.index('--watch')
        if idx + 1 < len(args):
            try: interval = int(args[idx + 1])
            except ValueError: pass
        print(f"Modo watch — actualizando cada {interval}s. Ctrl+C para detener.")
        while True:
            print(f"\n[{datetime.now(tz=TZ).strftime('%H:%M:%S')}] Fetching...")
            try: save(fetch())
            except Exception as e: print(f"  ERROR: {e}")
            time.sleep(interval)
    else:
        print(f"Fetching ocupacion CLRM03...")
        save(fetch())

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
procesar_wms_address.py
Convierte data/wms_address_CLRM03.json (scraped por Tampermonkey)
→ data/ocupacion_CLRM03.json (mismo formato que fetch_ocupacion.py)
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ        = ZoneInfo('America/Santiago')
WAREHOUSE = 'CLRM03'
BASE      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE, 'data')
IN_FILE   = os.path.join(DATA_DIR, 'wms_address_CLRM03.json')
OUT_FILE  = os.path.join(DATA_DIR, 'ocupacion_CLRM03.json')

CAP_BY_ZONE = {'RK': 6, 'BL': 1}

def parse_addr(addr):
    if not addr: return None
    p = addr.split('-')
    if len(p) < 6: return None
    try:
        return (p[0], int(p[2]), int(p[3]), int(p[4]), int(p[5]))
    except ValueError:
        return None

def process():
    if not os.path.exists(IN_FILE):
        print(f"  ERROR: no existe {IN_FILE}", flush=True)
        sys.exit(1)

    with open(IN_FILE, encoding='utf-8') as f:
        raw = json.load(f)

    print(f"  {len(raw):,} ubicaciones cargadas desde WMS", flush=True)

    slots = {}
    for r in raw:
        aid = r.get('address_id', '')
        if not aid:
            continue
        parsed = parse_addr(aid)
        if not parsed:
            continue
        zone = parsed[0]
        slots[aid] = {
            'id':    aid,
            'zone':  zone,
            'aisle': parsed[1], 'bay': parsed[2],
            'level': parsed[3], 'pos': parsed[4],
            'cap':   CAP_BY_ZONE.get(zone, 6),
            'qty':   r.get('stock', 0),
            'avail': r.get('available', 0),
            'res':   r.get('reserved', 0),
            'skus':  r.get('skus', []),
            'tipo':  '',
            'clase': '',
        }

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
        b['slots'].append({
            'id': s['id'], 'level': s['level'], 'pos': s['pos'],
            'tipo': s['tipo'], 'clase': s['clase'],
            'skus': s['skus'], 'qty': s['qty'], 'avail': s['avail'],
        })

    bays_list   = sorted(bays.values(), key=lambda b: (b['aisle'], b['bay']))
    total_slots = len(slots)
    all_skus    = [len(s['skus']) for s in slots.values()]
    locs_occ    = sum(1 for s in slots.values() if len(s['skus']) > 0)
    locs_multi  = sum(1 for s in slots.values() if len(s['skus']) > 1)

    stats = {
        'total_slots':     total_slots,
        'total_bays':      len(bays),
        'total_sku_slots': sum(all_skus),
        'locs_occ':        locs_occ,
        'locs_multi':      locs_multi,
        'pct_occ':   round(locs_occ   / total_slots * 100, 1) if total_slots else 0,
        'pct_multi': round(locs_multi / locs_occ   * 100, 1) if locs_occ   else 0,
        'avg_skus':  round(sum(all_skus) / len(all_skus), 2) if all_skus else 0,
        'max_skus':  max(all_skus) if all_skus else 0,
        'locs_full': sum(1 for s in slots.values() if len(s['skus']) >= s['cap']),
        'locs_1':    sum(1 for s in slots.values() if len(s['skus']) == 1),
        'locs_2':    sum(1 for s in slots.values() if len(s['skus']) == 2),
        'locs_3':    sum(1 for s in slots.values() if len(s['skus']) == 3),
        'locs_4plus':sum(1 for s in slots.values() if len(s['skus']) >= 4),
        'source':    'wms',
    }

    now = datetime.now(tz=TZ)
    out = {
        'date':    now.strftime('%Y-%m-%d'),
        'updated': now.strftime('%H:%M:%S'),
        'wh':      WAREHOUSE,
        'stats':   stats,
        'bays':    bays_list,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)

    kb = os.path.getsize(OUT_FILE) / 1024
    print(f"  Guardado: {OUT_FILE}  ({stats['total_slots']:,} slots · {stats['total_bays']:,} bays · {kb:.0f} KB)")
    print(f"  Ocupación: {stats['locs_occ']:,}/{stats['total_slots']:,} ({stats['pct_occ']}%)")

if __name__ == '__main__':
    process()

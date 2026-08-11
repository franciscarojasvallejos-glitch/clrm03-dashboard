#!/usr/bin/env python3
"""
procesar_wms_address.py
Hace merge de data/wms_address_CLRM03.json (WMS, Tampermonkey)
sobre data/ocupacion_CLRM03.json existente (BQ, layout completo).
Actualiza stock, SKUs e imágenes sin borrar el layout de BQ.
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ        = ZoneInfo('America/Santiago')
WAREHOUSE = 'CLRM03'
BASE      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE, 'data')
WMS_FILE  = os.path.join(DATA_DIR, 'wms_address_CLRM03.json')
OCC_FILE  = os.path.join(DATA_DIR, 'ocupacion_CLRM03.json')

def process():
    if not os.path.exists(WMS_FILE):
        print(f"  ERROR: no existe {WMS_FILE}", flush=True)
        sys.exit(1)
    if not os.path.exists(OCC_FILE):
        print(f"  ERROR: no existe {OCC_FILE} (ejecutar fetch_ocupacion.py primero)", flush=True)
        sys.exit(1)

    with open(WMS_FILE, encoding='utf-8') as f:
        wms_raw = json.load(f)
    with open(OCC_FILE, encoding='utf-8') as f:
        occ = json.load(f)

    print(f"  WMS: {len(wms_raw):,} ubicaciones con stock", flush=True)
    print(f"  BQ layout: {occ['stats']['total_slots']:,} slots, {occ['stats']['total_bays']:,} bays", flush=True)

    # Índice WMS por address_id
    wms_idx = {r['address_id']: r for r in wms_raw if r.get('address_id')}

    # Reconstruir índice de slots desde bays
    slot_updated = 0
    slot_cleared = 0
    for bay in occ['bays']:
        for slot in bay['slots']:
            sid = slot['id']
            if sid in wms_idx:
                w = wms_idx[sid]
                # Siempre actualizar stock desde WMS (dato en tiempo real)
                slot['qty']   = w.get('stock', 0)
                slot['avail'] = w.get('available', 0)
                # Solo actualizar SKUs si WMS los trae; si no, conservar los de BQ
                wms_skus = w.get('skus', [])
                if wms_skus:
                    slot['skus']        = wms_skus
                    slot['sku_details'] = w.get('sku_details', {})
                    # sku_qtys viene de BQ — se preserva si WMS no lo trae
                slot_updated += 1
            else:
                slot_cleared += 1  # sin cambios — BQ mantiene sus datos

        # Recalcular totales del bay
        bay['n_skus'] = sum(len(s['skus']) for s in bay['slots'])
        bay['qty']    = sum(s['qty']   for s in bay['slots'])
        bay['avail']  = sum(s['avail'] for s in bay['slots'])

    print(f"  Slots actualizados: {slot_updated:,} con stock, {slot_cleared:,} vaciados", flush=True)

    # Recalcular stats globales
    all_slots  = [s for bay in occ['bays'] for s in bay['slots']]
    total      = len(all_slots)
    locs_occ   = sum(1 for s in all_slots if len(s.get('skus', [])) > 0)
    locs_multi = sum(1 for s in all_slots if len(s.get('skus', [])) > 1)
    all_skus   = [len(s.get('skus', [])) for s in all_slots]

    occ['stats'].update({
        'locs_occ':        locs_occ,
        'locs_multi':      locs_multi,
        'pct_occ':   round(locs_occ   / total * 100, 1) if total else 0,
        'pct_multi': round(locs_multi / locs_occ * 100, 1) if locs_occ else 0,
        'avg_skus':  round(sum(all_skus) / len(all_skus), 2) if all_skus else 0,
        'max_skus':  max(all_skus) if all_skus else 0,
        'total_sku_slots': sum(all_skus),
        'locs_full': sum(1 for s in all_slots if len(s.get('skus', [])) >= s.get('cap', 6)),
        'locs_1':    sum(1 for s in all_slots if len(s.get('skus', [])) == 1),
        'locs_2':    sum(1 for s in all_slots if len(s.get('skus', [])) == 2),
        'locs_3':    sum(1 for s in all_slots if len(s.get('skus', [])) == 3),
        'locs_4plus':sum(1 for s in all_slots if len(s.get('skus', [])) >= 4),
        'source':    'wms_overlay',
    })

    now = datetime.now(tz=TZ)
    occ['updated'] = now.strftime('%H:%M:%S')
    occ['date']    = now.strftime('%Y-%m-%d')

    with open(OCC_FILE, 'w', encoding='utf-8') as f:
        json.dump(occ, f, separators=(',', ':'), ensure_ascii=False)

    kb = os.path.getsize(OCC_FILE) / 1024
    s  = occ['stats']
    print(f"  Guardado: {OCC_FILE} ({s['total_slots']:,} slots · {s['locs_occ']:,} ocupados · {s['pct_occ']}% · {kb:.0f} KB)")

if __name__ == '__main__':
    process()

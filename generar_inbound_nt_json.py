#!/usr/bin/env python3
"""
generar_inbound_nt_json.py
Genera data/inbound_nt_CLRM03.json con los Inbound Shipments que tienen
items NT (no_totable) actualmente en putaway pendiente en CLRM03.
Agrupa por IS con conteo NTB/TB, fecha agendada y progreso.
"""
import json, os, sys, io, time
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT  = 'meli-bi-data'
WH       = 'CLRM03'
SITE     = 'MLC'
BASE     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
OUT_FILE = os.path.join(DATA_DIR, f'inbound_nt_{WH}.json')

def generate():
    now = datetime.now(tz=ZoneInfo('America/Santiago'))
    print(f"[{now.strftime('%H:%M:%S')}] Generando inbound NT para {WH}...", flush=True)

    client = bigquery.Client(project=PROJECT)

    desde = (datetime.now(tz=ZoneInfo('America/Santiago')) - __import__('datetime').timedelta(days=60)).strftime('%Y-%m-%d')

    q = f"""
    SELECT
      op.INBOUND_ID                                                          AS is_id,
      ANY_VALUE(op.INB_APPOINTMENT_DATETIME)                                 AS appointment_dt,
      ANY_VALUE(op.INB_ARRIVAL_DATETIME)                                     AS arrival_dt,
      ANY_VALUE(op.INB_SHIPMENT_TYPE)                                        AS shipment_type,
      ANY_VALUE(op.INB_STATUS)                                               AS inb_status,
      SUM(op.INB_QUANTITY)                                                   AS qty_declared,
      SUM(op.INB_RCPT_TOTAL_QTY)                                             AS qty_received,
      SUM(COALESCE(op.CHKU_UNITS_OK,0) + COALESCE(op.CHKU_UNITS_DAMAGED,0)) AS qty_checkin,
      s.TOTABILITY,
      COUNT(DISTINCT op.INVENTORY_ID)                                        AS sku_count,
      COUNT(*)                                                               AS pw_items,
      0                                                                      AS waiting_start,
      0                                                                      AS waiting_finish,
      0                                                                      AS nt_movables,
      0                                                                      AS total_movables,
      MIN(op.CHK_CREATED_DATETIME)                                           AS oldest_pw
    FROM `{PROJECT}.WHOWNER.BT_FBM_INBOUND_OPERATION` op
    LEFT JOIN `{PROJECT}.WHOWNER.DM_SHP_ICQA_SKU_DETAILS` s
      ON op.INVENTORY_ID = s.INVENTORY_ID AND s.SIT_SITE_ID = '{SITE}'
    WHERE op.WAREHOUSE_ID = '{WH}'
      AND op.SIT_SITE_ID  = '{SITE}'
      AND op.AUD_INS_DT  >= '{desde}'
      AND op.CHECKIN_PUTAWAY = 'CHECKIN-SIN PUTAWAY'
    GROUP BY op.INBOUND_ID, s.TOTABILITY
    ORDER BY ANY_VALUE(op.INB_APPOINTMENT_DATETIME) ASC NULLS LAST
    """

    print("  Consultando BQ...", flush=True)
    rows = list(client.query(q).result())
    print(f"  {len(rows):,} filas obtenidas", flush=True)

    # Agrupar por IS — un IS puede tener NTB y TB
    by_is = {}
    for r in rows:
        is_id = str(int(r.is_id)) if r.is_id else ''
        if is_id not in by_is:
            by_is[is_id] = {
                'is_id':        is_id,
                'appointment':  r.appointment_dt.strftime('%Y-%m-%d %H:%M') if r.appointment_dt else None,
                'arrival':      r.arrival_dt.strftime('%Y-%m-%d %H:%M') if r.arrival_dt else None,
                'shipment_type': r.shipment_type or '',
                'inb_status':   r.inb_status or '',
                'qty_declared': int(r.qty_declared or 0),
                'qty_received': int(r.qty_received or 0),
                'qty_checkin':  int(r.qty_checkin or 0),
                'ntb_units':    0,
                'tb_units':     0,
                'ntb_skus':     0,
                'tb_skus':      0,
                'nt_movables':  int(r.nt_movables or 0),
                'total_movables': int(r.total_movables or 0),
                'waiting_start': int(r.waiting_start or 0),
                'waiting_finish': int(r.waiting_finish or 0),
                'oldest_pw':    r.oldest_pw.strftime('%Y-%m-%d %H:%M') if r.oldest_pw else None,
            }
        is_entry = by_is[is_id]
        units = int(r.pw_items or 0)
        skus  = int(r.sku_count or 0)
        if r.TOTABILITY == 'no_totable':
            is_entry['ntb_units'] += units
            is_entry['ntb_skus']  += skus
        else:
            is_entry['tb_units'] += units
            is_entry['tb_skus']  += skus

    # Calcular mins_until_sla (SLA = 48h desde appointment o arrival)
    tz = ZoneInfo('America/Santiago')
    result = []
    for entry in by_is.values():
        ref_str = entry.get('appointment') or entry.get('arrival')
        if ref_str:
            try:
                ref_dt = datetime.strptime(ref_str, '%Y-%m-%d %H:%M').replace(tzinfo=tz)
                sla_dt = ref_dt.replace(hour=ref_dt.hour)  # SLA 48h desde appointment
                mins_restantes = int((sla_dt.timestamp() - now.timestamp()) / 60) - 2880  # 48h
                entry['mins_restantes'] = mins_restantes
                if mins_restantes < 0:
                    entry['sla'] = 'over'
                elif mins_restantes < 1440:
                    entry['sla'] = 'warn'
                else:
                    entry['sla'] = 'ok'
            except:
                entry['mins_restantes'] = None
                entry['sla'] = 'unknown'
        else:
            entry['mins_restantes'] = None
            entry['sla'] = 'unknown'
        result.append(entry)

    # Ordenar: IS con NT primero, luego por appointment asc
    result.sort(key=lambda x: (
        0 if x['ntb_units'] > 0 else 1,
        x['appointment'] or '9999'
    ))

    ntb_is  = sum(1 for x in result if x['ntb_units'] > 0)
    total_u = sum(x['ntb_units'] for x in result)
    print(f"  {len(result)} IS · {ntb_is} con NT · {total_u:,} unidades NT", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {
        'updated': now.strftime('%Y-%m-%d %H:%M'),
        'total_is': len(result),
        'ntb_is':   ntb_is,
        'ntb_units': total_u,
        'shipments': result,
    }
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)
    kb = os.path.getsize(OUT_FILE) / 1024
    print(f"  Guardado: {OUT_FILE} ({kb:.0f} KB)", flush=True)

def main():
    args  = sys.argv[1:]
    watch = '--watch' in args
    interval = 3600
    if watch:
        idx = args.index('--watch')
        try: interval = int(args[idx+1])
        except: pass
        print(f"Modo watch — cada {interval}s. Ctrl+C para detener.", flush=True)
        while True:
            try: generate()
            except Exception as e: print(f"  ERROR: {e}", flush=True)
            time.sleep(interval)
    else:
        generate()

if __name__ == '__main__':
    main()

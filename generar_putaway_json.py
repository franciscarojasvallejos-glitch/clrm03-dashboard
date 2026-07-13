#!/usr/bin/env python3
"""
generar_putaway_json.py
Genera data/putaway_CLRM03.json con los movables pendientes de guardar
en CLRM03, directo desde BT_FBM_PUTAWAY.
PW_STATUS: WAITING_START = pendiente, WAITING_FINISH = en proceso
"""
import json, os, sys, io, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT  = 'meli-bi-data'
WH       = 'CLRM03'
BASE     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
OUT_FILE = os.path.join(DATA_DIR, f'putaway_{WH}.json')

SLA_WARN = 48 * 60  # 48h en minutos

def generate():
    now  = datetime.now(tz=ZoneInfo('America/Santiago'))
    tz   = ZoneInfo('America/Santiago')
    desde = (now - timedelta(days=60)).strftime('%Y-%m-%d')

    print(f"[{now.strftime('%H:%M:%S')}] Generando putaway pendiente {WH}...", flush=True)
    client = bigquery.Client(project=PROJECT)

    # Paso 1: movables pendientes desde BT_FBM_PUTAWAY
    q = f"""
    SELECT
      pw.ADDRESS_FROM                   AS movable,
      CAST(pw.SOURCE_PROCESS_ID AS STRING) AS is_id,
      pw.PW_STATUS,
      pw.FBM_USER_ID                    AS usuario,
      pw.AUD_INS_DT                     AS pw_created,
      pw.PW_CREATED_DATETIME            AS pw_created_dt,
      pw.PW_UPDATED_DATETIME            AS pw_updated_dt,
      COUNT(*)                          AS pw_items
    FROM `{PROJECT}.WHOWNER.BT_FBM_PUTAWAY` pw
    WHERE pw.WAREHOUSE_ID = '{WH}'
      AND pw.AUD_INS_DT  >= '{desde}'
      AND pw.PW_STATUS IN ('WAITING_START', 'WAITING_FINISH')
    GROUP BY 1,2,3,4,5,6,7
    ORDER BY pw.AUD_INS_DT ASC
    """

    print("  Consultando movables...", flush=True)
    rows = list(client.query(q).result())
    print(f"  {len(rows):,} movables pendientes", flush=True)

    # Paso 2: enriquecer con datos IS desde BT_FBM_INBOUND_OPERATION
    is_ids = list({r.is_id for r in rows if r.is_id})
    is_data = {}
    if is_ids:
        ids_str = ','.join(f"'{x}'" for x in is_ids[:500])
        q2 = f"""
        SELECT
          CAST(op.INBOUND_ID AS STRING)         AS is_id,
          ANY_VALUE(op.INB_APPOINTMENT_DATETIME) AS appointment_dt,
          ANY_VALUE(op.INB_ARRIVAL_DATETIME)     AS arrival_dt,
          ANY_VALUE(op.INB_SHIPMENT_TYPE)        AS shipment_type,
          ANY_VALUE(op.CUS_NICKNAME)             AS seller,
          COUNT(DISTINCT op.INVENTORY_ID)        AS sku_count,
          SUM(COALESCE(op.CHKU_UNITS_OK,0)+COALESCE(op.CHKU_UNITS_DAMAGED,0)) AS qty_checkin,
          MIN(op.CHK_CREATED_DATETIME)           AS oldest_chk
        FROM `{PROJECT}.WHOWNER.BT_FBM_INBOUND_OPERATION` op
        WHERE op.WAREHOUSE_ID = '{WH}'
          AND op.SIT_SITE_ID  = 'MLC'
          AND op.AUD_INS_DT  >= '{desde}'
          AND CAST(op.INBOUND_ID AS STRING) IN ({ids_str})
        GROUP BY 1
        """
        print("  Enriqueciendo con datos IS...", flush=True)
        for r2 in client.query(q2).result():
            is_data[r2.is_id] = r2

    def fmt(dt):
        if dt is None: return None
        if isinstance(dt, datetime):
            return dt.strftime('%Y-%m-%d %H:%M')
        return str(dt)[:16].replace('T', ' ')

    items = []
    for r in rows:
        is_nt = '-NT-' in (r.movable or '')
        inb   = is_data.get(r.is_id, None)

        now_naive = now.replace(tzinfo=None)

        # Tiempo en proceso: desde creación del movable en BT_FBM_PUTAWAY
        pw_dt = r.pw_created_dt
        if pw_dt:
            pw_naive = pw_dt.replace(tzinfo=None) if isinstance(pw_dt, datetime) else datetime.fromisoformat(str(pw_dt)).replace(tzinfo=None)
            mins_en_proceso = int((now_naive - pw_naive).total_seconds() / 60)
        else:
            mins_en_proceso = None

        # SLA = pw_created_dt + 48h - now  (igual que WMS: 48h desde creacion del movable)
        if pw_dt:
            deadline = pw_naive + timedelta(minutes=SLA_WARN)
            mins_restantes = int((deadline - now_naive).total_seconds() / 60)
            sla = 'over' if mins_restantes < 0 else ('warn' if mins_restantes < 120 else 'ok')
        else:
            mins_restantes = None
            sla = 'unknown'

        mins_since = mins_en_proceso  # alias para compatibilidad con dashboard

        items.append({
            'movable':        r.movable or '',
            'is_id':          r.is_id or '',
            'is_nt':          is_nt,
            'pw_status':      r.PW_STATUS or '',
            'usuario':        str(r.usuario or ''),
            'pw_items':       int(r.pw_items or 0),
            'shipment_type':  inb.shipment_type if inb else '',
            'seller':         inb.seller if inb else '',
            'sku_count':      int(inb.sku_count or 0) if inb else 0,
            'qty_checkin':    int(inb.qty_checkin or 0) if inb else 0,
            'appointment':    fmt(inb.appointment_dt) if inb else None,
            'arrival':        fmt(inb.arrival_dt) if inb else None,
            'pw_created':     fmt(r.pw_created_dt),
            'mins_en_proceso': mins_en_proceso,
            'mins_since_chk': mins_since,
            'mins_restantes': mins_restantes,
            'sla':            sla,
        })

    nt_movs  = sum(1 for x in items if x['is_nt'])
    over_sla = sum(1 for x in items if x['sla'] == 'over')
    print(f"  {len(items)} movables · {nt_movs} NT · {over_sla} sobre SLA", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {
        'updated':   now.strftime('%Y-%m-%d %H:%M'),
        'warehouse': WH,
        'total':     len(items),
        'nt_count':  nt_movs,
        'over_sla':  over_sla,
        'movables':  items,
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

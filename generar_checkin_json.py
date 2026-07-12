#!/usr/bin/env python3
"""
generar_checkin_json.py
Genera data/checkin_CLRM03.json con los items en proceso de check-in y putaway
de CLRM03 desde BT_FBM_INBOUND_OPERATION.

Uso:
  python generar_checkin_json.py               # hoy
  python generar_checkin_json.py --dias 3      # ultimos 3 dias
  python generar_checkin_json.py --watch 60    # modo watch cada 60s
  python generar_checkin_json.py --schema      # ver columnas
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
OUT_FILE = os.path.join(DATA_DIR, 'checkin_CLRM03.json')

SLA_WARN = 48 * 60   # minutos — 48h, sobre esto = rojo
SLA_OK   = 24 * 60   # minutos — 24h restantes, bajo esto = amarillo

def explore_schema(client):
    for table in ['BT_FBM_INBOUND_OPERATION']:
        print(f"\n=== {table} ===")
        q = f"""
        SELECT column_name, data_type
        FROM `{PROJECT}.WHOWNER.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = '{table}'
        ORDER BY ordinal_position
        """
        for r in client.query(q).result():
            print(f"  {r.column_name:<45} {r.data_type}")

def generate(dias=1):
    now    = datetime.now(tz=ZoneInfo('America/Santiago'))
    now_dt = now.replace(tzinfo=None)
    desde  = (now - timedelta(days=dias)).strftime('%Y-%m-%d')

    print(f"[{now.strftime('%H:%M:%S')}] Consultando check-in/putaway {WH} (desde {desde})...", flush=True)

    client = bigquery.Client(project=PROJECT)

    q = f"""
    SELECT
      op.INBOUND_ID,
      op.INVENTORY_ID,
      op.CUS_NICKNAME                            AS seller,
      op.INB_QUANTITY                            AS qty_declarada,
      COALESCE(op.CHKU_UNITS_OK, 0)
        + COALESCE(op.CHKU_UNITS_DAMAGED, 0)     AS qty_checkin,
      COALESCE(op.PW_UNITS_OK, 0)
        + COALESCE(op.PW_UNITS_DAMAGED, 0)       AS qty_putaway,
      op.CHK_FBM_USER_ID                         AS chk_user_id,
      op.PW_FBM_USER_ID                          AS pw_user_id,
      op.CHK_CREATED_DATETIME                    AS chk_ts,
      op.PW_CREATED_DATETIME                     AS pw_inicio_ts,
      op.PW_UPDATED_DATETIME                     AS pw_fin_ts,
      op.CHK_STATUS                              AS chk_status,
      op.PW_STATUS                               AS pw_status,
      op.CHECKIN_PUTAWAY                         AS estado,
      op.INB_SHIPMENT_TYPE                       AS tipo,
      pw.ADDRESS_FROM                            AS movable,
      pw.SOURCE_PROCESS_ID                       AS is_id,
      pw.FBM_USER_ID                             AS pw_user_wms
    FROM `{PROJECT}.WHOWNER.BT_FBM_INBOUND_OPERATION` op
    LEFT JOIN `{PROJECT}.WHOWNER.BT_FBM_PUTAWAY` pw
      ON pw.PUTAWAY_ID = op.PUTAWAY_ID
      AND pw.AUD_INS_DT >= '{desde}'
    WHERE op.WAREHOUSE_ID = '{WH}'
      AND op.SIT_SITE_ID  = 'MLC'
      AND op.AUD_INS_DT  >= '{desde}'
      AND (
        op.CHK_CREATED_DATETIME >= DATETIME '{desde} 00:00:00'
        OR op.PW_STATUS IN ('WAITING_STORAGE_TO', 'WAITING_FINISH')
      )
    ORDER BY
      CASE WHEN op.PW_STATUS IN ('WAITING_STORAGE_TO','WAITING_FINISH') THEN 0 ELSE 1 END,
      op.CHK_CREATED_DATETIME DESC
    LIMIT 5000
    """

    rows = list(client.query(q).result())
    print(f"  {len(rows):,} registros de inbound", flush=True)

    items = []
    for r in rows:
        chk_ts     = r.chk_ts
        pw_ini_ts  = r.pw_inicio_ts
        pw_fin_ts  = r.pw_fin_ts

        def to_naive(dt):
            if dt is None: return None
            if isinstance(dt, datetime):
                return dt.replace(tzinfo=None)
            return datetime.fromisoformat(str(dt)).replace(tzinfo=None)

        chk_naive = to_naive(chk_ts)
        pw_ini    = to_naive(pw_ini_ts)
        pw_fin    = to_naive(pw_fin_ts)

        mins_since_chk = int((now_dt - chk_naive).total_seconds() / 60) if chk_naive else None
        mins_to_pw     = int((pw_ini - chk_naive).total_seconds() / 60) if (pw_ini and chk_naive) else None
        mins_pw_dur    = int((pw_fin - pw_ini).total_seconds() / 60)    if (pw_fin and pw_ini) else None

        pw_status  = (r.pw_status or '')
        chk_status = (r.chk_status or '')
        estado     = (r.estado or '')

        en_putaway = pw_status in ('WAITING_STORAGE_TO', 'WAITING_FINISH')

        # Determinar SLA
        if pw_status == 'FINISHED':
            sla = 'done'
        elif en_putaway:
            # en putaway activo — usar tiempo desde inicio de putaway
            sla_mins = int((now_dt - pw_ini).total_seconds() / 60) if pw_ini else mins_since_chk
            if sla_mins is None:        sla = 'pw_active'
            elif sla_mins < SLA_OK:    sla = 'pw_active'
            elif sla_mins < SLA_WARN:  sla = 'pw_active'
            else:                       sla = 'over'
        elif mins_since_chk is None:
            sla = 'unknown'
        elif mins_since_chk < SLA_OK:
            sla = 'ok'
        elif mins_since_chk < SLA_WARN:
            sla = 'warn'
        else:
            sla = 'over'

        # Minutos restantes para SLA de 48h (negativo = ya venció)
        mins_restantes = (SLA_WARN - mins_since_chk) if mins_since_chk is not None else None

        def fmt_ts(dt):
            return str(dt)[:16].replace('T', ' ') if dt else ''

        items.append({
            'inbound_id':    str(r.INBOUND_ID or ''),
            'sku':           r.INVENTORY_ID or '',
            'seller':        r.seller or '',
            'qty_dec':       int(r.qty_declarada or 0),
            'qty_chk':       int(r.qty_checkin or 0),
            'qty_pw':        int(r.qty_putaway or 0),
            'chk_user':      str(r.chk_user_id or ''),
            'pw_user':       str(r.pw_user_id or ''),
            'chk_ts':        fmt_ts(chk_ts),
            'pw_inicio_ts':  fmt_ts(pw_ini_ts),
            'pw_fin_ts':     fmt_ts(pw_fin_ts),
            'chk_status':    r.chk_status or '',
            'pw_status':     r.pw_status or '',
            'estado':        r.estado or '',
            'tipo':          r.tipo or '',
            'movable':          r.movable or '',
            'is_id':            str(int(r.is_id)) if r.is_id else '',
            'mins_since_chk':   mins_since_chk,
            'mins_to_pw':       mins_to_pw,
            'mins_pw_dur':      mins_pw_dur,
            'mins_restantes':   mins_restantes,
            'en_putaway':       en_putaway,
            'sla':              sla,
        })

    en_pw      = sum(1 for x in items if x['en_putaway'])
    pendientes = sum(1 for x in items if x['sla'] in ('ok','warn','unknown'))
    over_sla   = sum(1 for x in items if x['sla'] == 'over')
    done       = sum(1 for x in items if x['sla'] == 'done')
    print(f"  En putaway: {en_pw} · Pendientes: {pendientes} · Sobre SLA: {over_sla} · Completos: {done}", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {
        'updated':      now.strftime('%Y-%m-%d %H:%M'),
        'warehouse':    WH,
        'sla_ok_min':   SLA_OK,
        'sla_warn_min': SLA_WARN,
        'stats': {
            'total':      len(items),
            'en_putaway': en_pw,
            'pendientes': pendientes,
            'over_sla':   over_sla,
            'done':       done,
        },
        'items': items,
    }
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)
    kb = os.path.getsize(OUT_FILE) / 1024
    print(f"  Guardado: {OUT_FILE} ({kb:.0f} KB)", flush=True)

def main():
    args     = sys.argv[1:]
    watch    = '--watch' in args
    schema   = '--schema' in args
    dias     = 1
    interval = 60

    if '--dias' in args:
        idx = args.index('--dias')
        try: dias = int(args[idx + 1])
        except: pass
    if '--watch' in args:
        idx = args.index('--watch')
        try: interval = int(args[idx + 1])
        except: pass

    client = bigquery.Client(project=PROJECT)

    if schema:
        explore_schema(client)
        return

    if watch:
        print(f"Modo watch — cada {interval}s. Ctrl+C para detener.", flush=True)
        while True:
            try: generate(dias)
            except Exception as e: print(f"  ERROR: {e}", flush=True)
            time.sleep(interval)
    else:
        generate(dias)

if __name__ == '__main__':
    main()

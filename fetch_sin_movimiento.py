#!/usr/bin/env python3
"""
fetch_sin_movimiento.py — Stock sin movimiento de picking CLRM03
Genera data/sin_movimiento_CLRM03.json

Corre 1 vez por día (no cada hora — consulta ~37 GB).
Uso: python fetch_sin_movimiento.py
"""
import json, os, sys, io
from datetime import datetime, date
from zoneinfo import ZoneInfo
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
from google.cloud import bigquery

PROJECT   = 'meli-bi-data'
WAREHOUSE = 'CLRM03'
TZ        = ZoneInfo('America/Santiago')
DIAS_VENTANA = 60   # ventana de búsqueda (días)
DIAS_ALERTA  = 30   # umbral para marcar como "sin movimiento"

def fetch():
    client = bigquery.Client(project=PROJECT)

    # Último picking por (address, SKU) en los últimos DIAS_VENTANA días
    q = f"""
    SELECT
      ADDRESS_FROM                        AS address_id,
      INVENTORY_ID,
      MAX(DATE(FBM_CREATED_DATE))         AS last_pick_date
    FROM `{PROJECT}.WHOWNER.BT_FBM_STOCK_3_MOVEMENT`
    WHERE WAREHOUSE_ID = '{WAREHOUSE}'
      AND DATE(FBM_CREATED_DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL {DIAS_VENTANA} DAY)
      AND FBM_REASON_PROCESS LIKE 'outbound_picking%'
      AND (UPPER(ADDRESS_FROM) LIKE 'RK%' OR UPPER(ADDRESS_FROM) LIKE 'BL%')
    GROUP BY address_id, INVENTORY_ID
    """
    print(f"  Consultando movimientos (~37 GB, ~$0.17)...", end=' ', flush=True)
    rows = list(client.query(q).result())
    print(f"{len(rows):,} registros")
    return rows

def save(rows):
    os.makedirs('data', exist_ok=True)
    today = datetime.now(tz=TZ).date()

    # mov_map: (address_id, sku) → last_pick_date
    mov_map = {}
    for r in rows:
        mov_map[(r.address_id, r.INVENTORY_ID)] = r.last_pick_date

    # Construir dict de slots con sus días sin movimiento
    # Cargamos el stock actual para cruzar
    path_occ = 'data/ocupacion_CLRM03.json'
    if not os.path.exists(path_occ):
        print("  ERROR: ocupacion_CLRM03.json no encontrado. Corre fetch_ocupacion.py primero.")
        sys.exit(1)

    with open(path_occ, encoding='utf-8') as f:
        occ = json.load(f)

    slots_out = {}   # address_id → {sku: dias}
    total_skus = 0
    alertas = 0

    for bay in occ['bays']:
        for slot in bay['slots']:
            aid = slot['id']
            if not slot['skus']:
                continue
            sku_dias = {}
            for sku in slot['skus']:
                last = mov_map.get((aid, sku))
                if last:
                    dias = (today - last).days
                else:
                    dias = DIAS_VENTANA  # no apareció en la ventana → al menos DIAS_VENTANA días
                sku_dias[sku] = dias
                total_skus += 1
                if dias >= DIAS_ALERTA:
                    alertas += 1
            slots_out[aid] = sku_dias

    now = datetime.now(tz=TZ)
    out = {
        'date': now.strftime('%Y-%m-%d'),
        'updated': now.strftime('%H:%M:%S'),
        'ventana_dias': DIAS_VENTANA,
        'alerta_dias': DIAS_ALERTA,
        'total_skus_con_stock': total_skus,
        'skus_en_alerta': alertas,
        'pct_alerta': round(alertas / total_skus * 100, 1) if total_skus else 0,
        'slots': slots_out,
    }

    path = 'data/sin_movimiento_CLRM03.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)
    kb = os.path.getsize(path) / 1024
    print(f"  Guardado: {path}  ({kb:.0f} KB)")
    print(f"  SKUs con stock: {total_skus:,}  |  En alerta (≥{DIAS_ALERTA}d): {alertas:,}  ({out['pct_alerta']}%)")

def main():
    print(f"Fetching sin_movimiento CLRM03 (ventana {DIAS_VENTANA}d, alerta ≥{DIAS_ALERTA}d)...")
    save(fetch())

if __name__ == '__main__':
    main()

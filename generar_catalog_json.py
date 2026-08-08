#!/usr/bin/env python3
"""
generar_catalog_json.py
Genera data/catalog_CLRM03.json con TODOS los Meli SKUs activos en CLRM03
(todas las áreas: RK, BL, MZ, etc.) consultando directamente BQ.
Incluye EAN, brand, domain, title, item_id e imagen.

Uso:
  python generar_catalog_json.py
  python generar_catalog_json.py --watch 3600
"""
import json, os, sys, io, time
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import bigquery

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT   = 'meli-bi-data'
SITE      = 'MLC'
WH        = 'CLRM03'
BASE      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE, 'data')
CAT_FILE  = os.path.join(DATA_DIR, 'catalog_CLRM03.json')

def load_catalog():
    if not os.path.exists(CAT_FILE):
        return {}
    with open(CAT_FILE, encoding='utf-8-sig') as f:
        return json.load(f).get('catalog', {})

def generate():
    now = datetime.now(tz=ZoneInfo('America/Santiago'))
    print(f"[{now.strftime('%H:%M:%S')}] Obteniendo todos los Meli SKUs de {WH}...", flush=True)

    catalog = load_catalog()
    print(f"  {len(catalog):,} SKUs en catálogo actual", flush=True)

    client = bigquery.Client(project=PROJECT)

    # ── Paso 1: obtener todos los inventory IDs activos en CLRM03 ─────────────
    q_skus = f"""
    SELECT DISTINCT INVENTORY_ID
    FROM `{PROJECT}.WHOWNER.BT_FBM_STOCK_3_ADDRESS`
    WHERE WAREHOUSE_ID = '{WH}'
      AND FBM_STOCK_STATUS = 'ok'
      AND FBM_QUANTITY > 0
    """
    print("  Consultando inventory IDs activos en BQ...", flush=True)
    all_skus = {r.INVENTORY_ID for r in client.query(q_skus).result() if r.INVENTORY_ID}
    print(f"  {len(all_skus):,} Meli SKUs activos en {WH}", flush=True)

    # ── Paso 2: cuáles necesitan datos — sin EAN o sin dimensiones ──────────
    def _falta_datos(entry):
        return not entry.get('ean') or 'dim_h' not in entry
    sin_datos = {sk for sk in all_skus if _falta_datos(catalog.get(sk, {}))}
    print(f"  {len(sin_datos):,} SKUs sin EAN/dimensiones — consultando DM_SHP_ICQA_SKU_DETAILS...", flush=True)

    nuevos = {}
    if sin_datos:
        lote_sz  = 800
        sin_list = list(sin_datos)
        total_lotes = (len(sin_list) + lote_sz - 1) // lote_sz
        for i in range(0, len(sin_list), lote_sz):
            lote    = sin_list[i:i+lote_sz]
            ids_str = ', '.join(f"'{x}'" for x in lote)
            q = f"""
            SELECT
              INVENTORY_ID,
              ITEM_ID,
              TITLE,
              BRAND,
              DOMAIN,
              COALESCE(EAN, GTIN) AS BARCODE,
              ITE_HEIGHT,
              ITE_LENGTH,
              ITE_WIDTH,
              ITE_WEIGHT
            FROM `{PROJECT}.WHOWNER.DM_SHP_ICQA_SKU_DETAILS`
            WHERE SIT_SITE_ID = '{SITE}'
              AND INVENTORY_ID IN ({ids_str})
            """
            n = i // lote_sz + 1
            print(f"  Lote {n}/{total_lotes} ({len(lote)} IDs)...", flush=True)
            for r in client.query(q).result():
                entry = catalog.get(r.INVENTORY_ID, {}).copy()
                if r.BARCODE: entry['ean']    = r.BARCODE
                if r.BRAND:   entry['brand']  = r.BRAND
                if r.DOMAIN:  entry['domain'] = r.DOMAIN
                if r.TITLE and not entry.get('title'):
                    entry['title'] = r.TITLE
                if r.ITEM_ID and not entry.get('item_id'):
                    entry['item_id'] = f"MLC{r.ITEM_ID}"
                if r.ITE_HEIGHT is not None: entry['dim_h']  = round(float(r.ITE_HEIGHT), 1)
                if r.ITE_LENGTH is not None: entry['dim_l']  = round(float(r.ITE_LENGTH), 1)
                if r.ITE_WIDTH  is not None: entry['dim_w']  = round(float(r.ITE_WIDTH),  1)
                if r.ITE_WEIGHT is not None: entry['dim_kg'] = round(float(r.ITE_WEIGHT), 3)
                nuevos[r.INVENTORY_ID] = entry

        # SKUs que DM_SHP_ICQA no conoce → marcar ean vacío para no re-consultar
        for sk in sin_list:
            if sk not in nuevos:
                base = catalog.get(sk, {}).copy()
                base.setdefault('ean', '')
                base.setdefault('dim_h', None)
                nuevos[sk] = base

        # Merge al catálogo
        catalog.update(nuevos)
        print(f"  {len(nuevos):,} SKUs actualizados", flush=True)

    # ── Paso 3: guardar solo SKUs activos en el warehouse ────────────────────
    catalog_out = {}
    for sk in all_skus:
        catalog_out[sk] = catalog.get(sk, {'ean': '', 'brand': '', 'domain': ''})

    con_ean   = sum(1 for v in catalog_out.values() if v.get('ean'))
    con_title = sum(1 for v in catalog_out.values() if v.get('title'))
    print(f"  Con EAN: {con_ean:,} · Con título: {con_title:,} / {len(catalog_out):,}", flush=True)

    # ── Paso 4: imágenes via BQ (DM_SHP_ICQA_INVENTORY_THUMBNAIL_ALL_LATAM) ──
    sin_img = [sk for sk in catalog_out if not catalog_out[sk].get('img')]
    if sin_img:
        print(f"  Buscando imágenes para {len(sin_img):,} SKUs en BQ...", flush=True)
        lote_sz = 800
        ok = 0
        total_lotes = (len(sin_img) + lote_sz - 1) // lote_sz
        for i in range(0, len(sin_img), lote_sz):
            lote    = sin_img[i:i+lote_sz]
            ids_str = ', '.join(f"'{x}'" for x in lote)
            q_img = f"""
            SELECT ITE_ITEM_INVENTORY_ID, ITE_ITEM_THUMBNAIL
            FROM `{PROJECT}.WHOWNER.DM_SHP_ICQA_INVENTORY_THUMBNAIL_ALL_LATAM`
            WHERE SIT_SITE_ID = '{SITE}'
              AND ITE_ITEM_INVENTORY_ID IN ({ids_str})
              AND ITE_ITEM_THUMBNAIL IS NOT NULL
              AND ITE_ITEM_THUMBNAIL != ''
            """
            n = i // lote_sz + 1
            if n % 10 == 1:
                print(f"    Lote {n}/{total_lotes}...", flush=True)
            for r in client.query(q_img).result():
                inv_id = r.ITE_ITEM_INVENTORY_ID
                thumb  = r.ITE_ITEM_THUMBNAIL
                if inv_id in catalog_out and thumb:
                    thumb = thumb.replace('http://', 'https://')
                    catalog_out[inv_id]['img'] = thumb
                    ok += 1
        print(f"  {ok:,} imágenes obtenidas de BQ", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = {
        'updated':  now.strftime('%Y-%m-%d %H:%M'),
        'total':    len(catalog_out),
        'con_ean':  con_ean,
        'catalog':  catalog_out,
    }
    with open(CAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)
    kb = os.path.getsize(CAT_FILE) / 1024
    print(f"  Guardado: {CAT_FILE} ({kb:.0f} KB)", flush=True)

def main():
    args     = sys.argv[1:]
    watch    = '--watch' in args
    interval = 3600
    if watch:
        idx = args.index('--watch')
        try: interval = int(args[idx + 1])
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

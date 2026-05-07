import streamlit as st
import pandas as pd
import requests
import urllib.parse
import time
from geopy.distance import geodesic

@st.cache_data(ttl=86400)
def geocode_address(street, city):
    if not street or str(street).lower() in ['nan', 'none', '']: return None, None
    street_clean = str(street).split(',')[0].strip()
    city_clean = str(city).strip()
    queries = [f"{street_clean}, {city_clean}, Greece", f"{street_clean}, Greece"]
    for q in queries:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q)}&format=json&limit=1"
        try:
            r = requests.get(url, headers={'User-Agent': 'AlumilLogisticsApp/1.0'}, timeout=4).json()
            if r: return float(r[0]['lat']), float(r[0]['lon'])
            time.sleep(0.3)
        except: pass
    return None, None
    
@st.cache_data(ttl=3600)
def get_osrm_data(coords):
    if not coords or len(coords) < 2: return None, 0, 0
    locs = ";".join([f"{lon},{lat}" for lat, lon in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{locs}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10).json()
        if r['code'] == 'Ok':
            return r['routes'][0]['geometry']['coordinates'], r['routes'][0]['distance']/1000, r['routes'][0]['duration']/60
    except: pass
    return None, 0, 0

def clean_val(v):
    if pd.isna(v): return 0.0
    if isinstance(v, (int, float)): return float(v)
    v_str = str(v).strip()
    if not v_str or v_str.lower() in ['nan', 'none', '']: return 0.0
    if ',' in v_str: v_str = v_str.replace('.', '').replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def gr_num(val, decimals=1):
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

@st.cache_data(ttl=60)
def get_supplier_pickups():
    try:
        df = conn.read(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", ttl=10)
        return df
    except:
        return pd.DataFrame()
@st.cache_data(ttl=600)
def get_cached_geodesic(p1, p2):
    return geodesic(p1, p2).km

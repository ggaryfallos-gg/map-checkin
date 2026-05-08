import streamlit as st
import pandas as pd
import pydeck as pdk
import requests
import urllib.parse
import time
from geopy.distance import geodesic

LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"


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
    
    # Αν το string περιέχει γράμματα (όπως η πινακίδα), σταμάτα αμέσως
    if any(c.isalpha() for c in v_str if c not in [',', '.']): 
        return 0.0
        
    if ',' in v_str: 
        v_str = v_str.replace('.', '').replace(',', '.')
    try: 
        return float(v_str)
    except ValueError:
        return 0.0

def gr_num(val, decimals=1):
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

@st.cache_data(ttl=60)
def get_supplier_pickups(_conn, log_url, force_refresh=False): # Προσθήκη παραμέτρου
    if force_refresh:
        st.cache_data.clear() # Καθαρίζει την cache για να τραβήξει τα φρέσκα
    try:
        df = _conn.read(spreadsheet=log_url, worksheet="Supplier_Pickups", ttl=0)
        return df if df is not None else pd.DataFrame()
    except:
        return pd.DataFrame()
        
@st.cache_data(ttl=600)
def get_cached_geodesic(p1, p2):
    return geodesic(p1, p2).km


def render_public_tracking(plate, _conn, log_url):
    st.title(f"📍 Live Route: {plate}")
    
    try:
        df = _conn.read(spreadsheet=log_url, worksheet="Transit_Log", ttl=0)
        
        if df is not None and not df.empty:
            df.columns = [c.strip() for c in df.columns]
            df['SearchPlate'] = df['Plate'].str.replace(' ', '')
            target_plate = plate.replace(' ', '')
            
            truck_logs = df[df['SearchPlate'] == target_plate].copy()
            
            if not truck_logs.empty:
                truck_logs['Timestamp'] = pd.to_datetime(truck_logs['Timestamp'])
                truck_logs = truck_logs.sort_values('Timestamp')
                last_5_df = truck_logs.tail(10)
                
                # --- ΕΔΩ ΟΡΙΖΟΝΤΑΙ ΤΑ LAYERS ---
                path_data = [{"path": last_5_df[['Longitude', 'Latitude']].values.tolist(), "color": [0, 102, 204, 255]}]
                current_pos = last_5_df.tail(1)

                layers = [
                    pdk.Layer(
                        "PathLayer", path_data, get_path="path", 
                        get_width=10, get_color="color", width_min_pixels=3
                    ),
                    pdk.Layer(
                        "ScatterplotLayer", current_pos, 
                        get_position=['Longitude', 'Latitude'], 
                        get_color=[255, 0, 0], get_radius=50, radius_min_pixels=6
                    )
                ]

                view_state = pdk.ViewState(
                    latitude=current_pos['Latitude'].iloc[0],
                    longitude=current_pos['Longitude'].iloc[0],
                    zoom=12, pitch=0
                )

                # --- ΤΟ PYDECK ΠΡΕΠΕΙ ΝΑ ΕΙΝΑΙ ΜΕΣΑ ΣΤΟ IF ΠΟΥ ΟΡΙΖΕΙ ΤΑ LAYERS ---
                st.pydeck_chart(pdk.Deck(
                    layers=layers,
                    initial_view_state=view_state,
                    map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
                    tooltip={"html": "<b>Πινακίδα:</b> {Plate}", "style": {"color": "white"}}
                ))
                
                st.subheader("📋 Τελευταία 5 Στίγματα")
                st.dataframe(last_5_df.sort_values('Timestamp', ascending=False)[['Timestamp', 'Latitude', 'Longitude']], hide_index=True)
                
            else:
                st.warning(f"Δεν βρέθηκαν στίγματα για το όχημα {plate}.")
        else:
            st.error("Το Transit_Log είναι άδειο.")

    except Exception as e:
        st.error(f"Debug Error: {e}")

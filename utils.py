import streamlit as st
import pandas as pd
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
    st.title(f"📍 Live Tracking: {plate}")
    
    try:
        # 1. Ανάγνωση δεδομένων
        df = _conn.read(spreadsheet=log_url, worksheet="Transit_Log", ttl=0)
        
        if df is not None and not df.empty:
            df.columns = [c.strip() for c in df.columns]
            
            # 2. Φιλτράρισμα και Καθαρισμός Πινακίδας
            df['SearchPlate'] = df['Plate'].str.replace(' ', '')
            target_plate = plate.replace(' ', '')
            truck_logs = df[df['SearchPlate'] == target_plate].copy()
            
            if not truck_logs.empty:
                # Μετατροπή Timestamp σε datetime για σωστό sorting
                truck_logs['Timestamp'] = pd.to_datetime(truck_logs['Timestamp'])
                truck_logs = truck_logs.sort_values('Timestamp', ascending=True)
                
                # Κράτα τα τελευταία 5 για τον πίνακα (σε φθίνουσα σειρά)
                last_5_points = truck_logs.tail(5).sort_values('Timestamp', ascending=False)
                
                # 3. Metrics (Τελευταίο Στίγμα)
                last_pos = truck_logs.iloc[-1]
                c1, c2 = st.columns(2)
                c1.metric("Τελευταία Ενημέρωση", last_pos['Timestamp'].strftime('%H:%M:%S'))
                c2.metric("Κατάσταση", "Εν Κινήσει")

                # 4. Σχεδίαση Διαδρομής (Route)
                # Χρησιμοποιούμε το st.map για τα σημεία και το st.line_chart για την "τάση" 
                # ή το pydeck για πραγματική γραμμή διαδρομής
                st.subheader("🗺️ Διαδρομή Οχήματος")
                map_data = truck_logs[['Latitude', 'Longitude']].rename(columns={'Latitude': 'lat', 'Longitude': 'lon'})
                st.map(map_data, zoom=12)

                # 5. Πίνακας με τα 5 τελευταία σημεία
                st.subheader("📋 Τελευταία 5 Στίγματα")
                st.table(last_5_points[['Timestamp', 'Latitude', 'Longitude']].assign(
                    Timestamp=lambda x: x['Timestamp'].dt.strftime('%d/%m %H:%M')
                ))

                st.success(f"Η διαδρομή του {plate} ανανεώθηκε.")
            else:
                st.warning(f"Δεν βρέθηκαν δεδομένα για την πινακίδα: {plate}")
        else:
            st.error("Το Transit_Log είναι άδειο.")
            
    except Exception as e:
        st.error(f"Σφάλμα: {e}")
    
    st.divider()
    st.caption("Powered by Alumil Logistics System")

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


def render_public_tracking(plate, _conn,log_url):
    st.set_page_config(page_title=f"Live Tracking - {plate}", layout="centered")
    
    # Industrial Style Header
    st.title(f"📍 Παρακολούθηση Οχήματος: {plate}")
    
    # Διάβασμα του Transit_Log (χωρίς cache για να είναι live)
    try:
        # 1. Διάβασμα των logs
        df = _conn.read(spreadsheet=log_url, worksheet="Transit_Log", ttl=0)
        
        if df is not None and not df.empty:
            # Καθαρισμός επικεφαλίδων (για σιγουριά)
            df.columns = [c.strip() for c in df.columns]
            
            # 2. Φιλτράρισμα για την πινακίδα (Προσοχή: strip για να πιάσει KIE 6761 και KIE6761)
            # Δημιουργούμε μια προσωρινή στήλη χωρίς κενά για το search
            df['SearchPlate'] = df['Plate'].str.replace(' ', '')
            target_plate = plate.replace(' ', '')
            
            truck_logs = df[df['SearchPlate'] == target_plate].tail(1)
            
            if not truck_logs.empty:
                last_pos = truck_logs.iloc[0]
                
                # 3. Εμφάνιση Metrics
                c1, c2 = st.columns(2)
                # Χρησιμοποιούμε 'Timestamp' (όπως είναι στο Sheet σου)
                c1.metric("Τελευταίο Στίγμα", str(last_pos['Timestamp']))
                c2.metric("Κατάσταση", "Εν Κινήσει")
                
                # 4. Χάρτης με τις στήλες Latitude & Longitude
                if 'Latitude' in last_pos and 'Longitude' in last_pos:
                    import pandas as pd
                    try:
                        lat = float(last_pos['Latitude'])
                        lon = float(last_pos['Longitude'])
                        map_df = pd.DataFrame({'lat': [lat], 'lon': [lon]})
                        
                        # Εμφάνιση Χάρτη
                        st.map(map_df, zoom=12)
                        
                        st.success(f"Το όχημα {plate} εντοπίστηκε επιτυχώς.")
                    except Exception as e:
                        st.warning(f"Πρόβλημα στις συντεταγμένες: {e}")
                else:
                    st.warning("Δεν βρέθηκαν γεωγραφικά δεδομένα (Latitude/Longitude).")
            else:
                st.warning(f"Δεν βρέθηκαν πρόσφατα logs για την πινακίδα: {plate}")
        else:
            st.error("Το αρχείο καταγραφής (Transit_Log) είναι άδειο.")
            
    except Exception as e:
        st.error(f"Σφάλμα ανάγνωσης δεδομένων: {e}")
        # Debugging: Δείξε μας τι διαβάζει το app
        if 'df' in locals():
            st.write("Στήλες που βρέθηκαν στο Sheet:", list(df.columns))
    
    st.divider()
    st.caption("Powered by Alumil Logistics System")

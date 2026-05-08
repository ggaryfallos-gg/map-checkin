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
        # Διάβασμα των logs
        df = _conn.read(spreadsheet=log_url, worksheet="Transit_Log", ttl=0)
        
        if df is not None and not df.empty:
            # Καθαρισμός ονομάτων στηλών (αφαίρεση κενών)
            df.columns = [c.strip() for c in df.columns]
            
            # Φιλτράρισμα για τη συγκεκριμένη πινακίδα
            truck_logs = df[df['Plate'] == plate].tail(1)
            
            if not truck_logs.empty:
                last_pos = truck_logs.iloc[0]
                
                # Metrics
                c1, c2 = st.columns(2)
                c1.metric("Τελευταία Ενημέρωση", last_pos.get('Timestamp', 'N/A'))
                c2.metric("Κατάσταση", "Καθ' οδόν")
                
                # Έλεγχος για τη στήλη Location ή εναλλακτικές
                loc = last_pos.get('Location') or last_pos.get('location') or "Μη διαθέσιμη τοποθεσία"
                st.info(f"📍 Τρέχουσα Περιοχή: **{loc}**")
                
                # Αν υπάρχουν συντεταγμένες, δείξε χάρτη
                if 'Lat' in last_pos and 'Lon' in last_pos:
                    try:
                        import pandas as pd
                        lat = float(str(last_pos['Lat']).replace(',', '.'))
                        lon = float(str(last_pos['Lon']).replace(',', '.'))
                        map_df = pd.DataFrame({'lat': [lat], 'lon': [lon]})
                        st.map(map_df)
                    except:
                        st.warning("Αδυναμία απεικόνισης χάρτη (Λάθος συντεταγμένες).")
            else:
                st.warning(f"Δεν βρέθηκαν πρόσφατα δεδομένα για το όχημα {plate}.")
        else:
            st.error("Το αρχείο καταγραφής είναι άδειο.")
            
    except Exception as e:
        st.error(f"Debug Error: {e}")
        # Δείξε μας τις στήλες για να ξέρουμε τι φταίει
        if 'df' in locals():
            st.write("Διαθέσιμες στήλες στο Sheet:", list(df.columns))
    
    st.divider()
    st.caption("Powered by Alumil Logistics System")

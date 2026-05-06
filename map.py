import streamlit as st
import pandas as pd
import folium
import requests
import urllib.parse
import time
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.distance import geodesic
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# --- CONFIG & TIMEZONE ---
st.set_page_config(page_title="Alumil Logistics Hub v31", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# --- LINKS ΤΩΝ GOOGLE SHEETS ---
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"
CUSADDRESS_URL = "https://docs.google.com/spreadsheets/d/1k9-gCuo_BxVezLaVoagh04xfUoXfj26aH2Mf833qGMk/edit" 
DELIVERIES_URL = "https://docs.google.com/spreadsheets/d/10uKgg3AIuSnROK2-6VnY0Rm3U4vH2xv8O4OFthgaWww/edit"

# --- SESSION STATE INIT ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "loading_date" not in st.session_state: st.session_state.loading_date = None
if "username" not in st.session_state: st.session_state.username = None
if "route_data" not in st.session_state: st.session_state.route_data = []
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "start_time" not in st.session_state: st.session_state.start_time = None
if "draft_sequence" not in st.session_state: st.session_state.draft_sequence = None
# Νέα State Variables για τα διαδραστικά φίλτρα
if "filter_plate" not in st.session_state: st.session_state.filter_plate = "Όλα"
if "filter_date" not in st.session_state: st.session_state.filter_date = "Όλες"

# --- LOGIN SCREEN ---
def check_password():
    if st.session_state.password_correct: return True
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Προσωπικός Κωδικός", type="password")
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    return False

if not check_password(): st.stop()

# --- UTILITIES ---
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

def get_osrm_data(coords):
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
    if ',' in v_str:
        v_str = v_str.replace('.', '').replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def gr_num(val, decimals=1):
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

# --- DATA PIPELINE ---
@st.cache_data(ttl=300)
def load_full_data():
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship.columns = ship.columns.str.strip()
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    ship['Delivery'] = ship['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
    
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    try:
        dels = conn.read(spreadsheet=DELIVERIES_URL, ttl=300)
        dels.columns = dels.columns.str.strip()
        dels['Delivery'] = dels['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
        dels_sub = dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery')
        ship = pd.merge(ship, dels_sub, on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη Ημ/νία').astype(str)
        ship['Loading_Date'] = ship['Loading_Date'].replace(['nan', 'NaT', 'None', ''], 'Άγνωστη Ημ/νία')
    except:
        ship['Loading_Date'] = 'Άγνωστη Ημ/νία'

    try:
        cus_df = conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
        cus_df.columns = cus_df.columns.str.strip()
        for col in ['Name', 'Street', 'Telephone 1', 'Postal Code', 'Latitude', 'Longitude']:
            if col not in cus_df.columns: cus_df[col] = ''
        
        cus_df['Latitude'] = pd.to_numeric(cus_df['Latitude'], errors='coerce')
        cus_df['Longitude'] = pd.to_numeric(cus_df['Longitude'], errors='coerce')
        
        cus_df_sub = cus_df[['Name', 'Street', 'Telephone 1', 'Postal Code', 'Latitude', 'Longitude']].drop_duplicates('Name')
        cus_df_sub = cus_df_sub.rename(columns={'Latitude': 'Lat_exact', 'Longitude': 'Lon_exact'})
        df = pd.merge(ship, cus_df_sub, on='Name', how='left')
    except:
        df = ship.copy()
        df['Street'], df['Telephone 1'], df['Postal Code'], df['Lat_exact'], df['Lon_exact'] = '', '', '', None, None

    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords.columns = coords.columns.str.strip()
    coords['City_Match'] = coords['City'].astype(str).str.strip().str.upper()
    coords = coords.rename(columns={'Latitude': 'Lat_city', 'Longitude': 'Lon_city'})
    df = pd.merge(df, coords.drop_duplicates('City_Match'), left_on='City_Clean', right_on='City_Match', how='left')
    
    df['Final_Lat'] = df['Lat_exact'].fillna(df['Lat_city'])
    df['Final_Lon'] = df['Lon_exact'].fillna(df['Lon_city'])

    counts = df.groupby(['Plate_Clean', 'Loading_Date'])['Name'].nunique().reset_index(name='Dests')
    unique_routes = df[['Truck License Plate', 'Plate_Clean', 'Loading_Date']].drop_duplicates()
    fleet_summary = pd.merge(unique_routes, counts, on=['Plate_Clean', 'Loading_Date'], how='left').fillna(0)
    
    return fleet_summary, df

fleet_info, all_data = load_full_data()

# --- ΣΥΝΑΡΤΗΣΗ ΕΠΑΝΑΦΟΡΑΣ ΜΝΗΜΗΣ ---
def reset_shift():
    st.session_state.user_plate = None
    st.session_state.loading_date = None
    st.session_state.draft_sequence = None
    st.session_state.route_data = []
    st.session_state.route_geom = None
    st.session_state.start_time = None
    st.session_state.filter_plate = "Όλα"
    st.session_state.filter_date = "Όλες"

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
st.sidebar.write(f"👤 **{st.session_state.username}**")

if st.session_state.user_plate is not None:
    st.sidebar.divider()
    if st.sidebar.button("🔄 Αλλαγή Οχήματος / Ημ/νίας", use_container_width=True):
        reset_shift()
        st.rerun()

st.sidebar.divider()
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

if st.sidebar.button("🚪 Logout"):
    st.session_state.password_correct = False
    reset_shift()
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        
        # --- BULLETPROOF ΑΛΛΗΛΕΞΑΡΤΩΜΕΝΑ DROPDOWNS ---
        if st.session_state.filter_date != "Όλες":
            plates_raw = fleet_info[fleet_info['Loading_Date'] == st.session_state.filter_date]['Truck License Plate'].dropna().astype(str).unique()
        else:
            plates_raw = fleet_info['Truck License Plate'].dropna().astype(str).unique()
        avail_plates = ["Όλα"] + sorted(plates_raw.tolist())

        if st.session_state.filter_plate != "Όλα":
            dates_raw = fleet_info[fleet_info['Truck License Plate'] == st.session_state.filter_plate]['Loading_Date'].dropna().astype(str).unique()
        else:
            dates_raw = fleet_info['Loading_Date'].dropna().astype(str).unique()
        avail_dates = ["Όλες"] + sorted(dates_raw.tolist())

        if st.session_state.filter_plate not in avail_plates: st.session_state.filter_plate = "Όλα"
        if st.session_state.filter_date not in avail_dates: st.session_state.filter_date = "Όλες"

        col1, col2 = st.columns(2)
        plate_sel = col1.selectbox("🚚 Επιλέξτε Φορτηγό", avail_plates, index=avail_plates.index(st.session_state.filter_plate))
        date_sel = col2.selectbox("📅 Ημ/νία Φόρτωσης", avail_dates, index=avail_dates.index(st.session_state.filter_date))

        if plate_sel != st.session_state.filter_plate or date_sel != st.session_state.filter_date:
            st.session_state.filter_plate = plate_sel
            st.session_state.filter_date = date_sel
            st.rerun()

        st.divider()

        if plate_sel != "Όλα" and date_sel != "Όλες":
            selected_route = fleet_info[(fleet_info['Truck License Plate'] == plate_sel) & (fleet_info['Loading_Date'] == date_sel)].iloc[0]
            st.success(f"✅ Επιτυχής Επιλογή: Προγραμματισμένες Στάσεις Δρομολογίου: **{int(selected_route['Dests'])}**")
            
            if st.button("🚀 Έναρξη Βάρδιας", type="primary", use_container_width=True):
                st.session_state.user_plate = selected_route['Plate_Clean']
                st.session_state.loading_date = selected_route['Loading_Date']
                st.session_state.display_plate = selected_route['Truck License Plate']
                st.rerun()
        else:
            st.info("ℹ️ Παρακαλώ επιλέξτε **Φορτηγό** και **Ημερομηνία** για να ξεκινήσετε.")

    else:
        st.subheader(f"🚚 {st.session_state.display_plate} (Φόρτωση: {st.session_state.loading_date})")
        
        user_data = all_data[(all_data['Plate_Clean'] == st.session_state.user_plate) & (all_data['Loading_Date'] == st.session_state.loading_date)].copy()
        
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        # --- GEOCODING & AUTO-SAVE LOGIC ---
        new_coords_batch = []
        for idx, row in user_data.iterrows():
            street = str(row.get('Street', ''))
            city = str(row.get('City_x', ''))
            
            if street and street.lower() not in ['nan', 'none']:
                user_data.at[idx, 'Display_Address'] = f"{street}, {city}"
            else:
                user_data.at[idx, 'Display_Address'] = city

            if pd.isna(row.get('Lat_exact')) and street and street.lower() not in ['nan', 'none']:
                lat, lon = geocode_address(street, city)
                if lat and lon:
                    user_data.at[idx, 'Final_Lat'] = lat
                    user_data.at[idx, 'Final_Lon'] = lon
                    user_data.at[idx, 'Lat_exact'] = lat 
                    new_coords_batch.append({'Name': row['Name'], 'Latitude': lat, 'Longitude': lon})

        if new_coords_batch:
            with st.spinner("Αποθήκευση νέων συντεταγμένων στη βάση..."):
                fresh_cus = conn.read(spreadsheet=CUSADDRESS_URL, ttl=0)
                fresh_cus.columns = fresh_cus.columns.str.strip()
                if 'Latitude' not in fresh_cus.columns: fresh_cus['Latitude'] = ''
                if 'Longitude' not in fresh_cus.columns: fresh_cus['Longitude'] = ''
                
                for update in new_coords_batch:
                    mask = fresh_cus['Name'] == update['Name']
                    if mask.any():
                        fresh_cus.loc[mask, 'Latitude'] = update['Latitude']
                        fresh_cus.loc[mask, 'Longitude'] = update['Longitude']
                
                conn.update(spreadsheet=CUSADDRESS_URL, data=fresh_cus)
                load_full_data.clear() 
                st.toast(f"✅ Αποθηκεύτηκαν μόνιμα {len(new_coords_batch)} νέες διευθύνσεις!")

        # --- UI TABS ---
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["🌎 Γενικός Χάρτης", "🛣️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Ειδοποίηση"])

        with tab1:
            st.write("Σημεία εκφόρτωσης:")
            m1 = folium.Map(location=curr_loc, zoom_start=7)
            folium.Marker(curr_loc, popup="Η θέση μου", icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m1)
            
            for _, r in user_data.drop_duplicates(subset=['Name']).iterrows():
                if pd.notna(r['Final_Lat']):
                    phone_raw = str(r.get('Telephone 1', ''))
                    tel_html = f"<br><br><a href='tel:{''.join(c for c in phone_raw if c.isdigit() or c == '+')}' style='background-color:#28a745; color:white; padding:6px 12px; text-decoration:none; border-radius:5px; display:inline-block; font-weight:bold;'>📞 Κλήση: {phone_raw}</a>" if phone_raw and phone_raw.lower() not in ['nan', 'none', ''] else ""
                    
                    kg_info = f"<br>📦 Φορτίο: {gr_num(r.get('Total KG', 0), 1)} KG"
                    popup_content = f"<b>{r['Name']}</b><br>{r.get('Display_Address', '')}{kg_info}{tel_html}"
                    
                    folium.Marker(
                        [r['Final_Lat'], r['Final_Lon']], 
                        popup=folium.Popup(popup_content, max_width=300), 
                        tooltip=f"{r['Name']}"
                    ).add_to(m1)
            st_folium(m1, width="100%", height=500, key="all_points_map")

        with tab2:
            st.subheader("Σχεδιασμός & Βελτιστοποίηση")
            
            if st.button("1. Αυτόματη Πρόταση Σειράς (OSRM)", use_container_width=True):
                stops = user_data.drop_duplicates('Name').dropna(subset=['Final_Lat'])
                pts, seq_list = [curr_loc], []
                unvisited = stops.copy()
                
                while not unvisited.empty:
                    unvisited['d'] = unvisited.apply(lambda r: geodesic(pts[-1], (r['Final_Lat'], r['Final_Lon'])).km, axis=1)
                    idx = unvisited['d'].idxmin()
                    row = unvisited.loc[idx]
                    pts.append((row['Final_Lat'], row['Final_Lon']))
                    un_time = (row['Total KG'] / 1000) * 10
                    seq_list.append({
                        'name': row['Name'], 'address': row['Display_Address'], 'telephone': str(row.get('Telephone 1', '')),
                        'kg': row['Total KG'], 'unload': un_time, 'coords': (row['Final_Lat'], row['Final_Lon'])
                    })
                    unvisited = unvisited.drop(index=idx)
                
                draft_df = pd.DataFrame([{
                    'Name': s['name'], 'Address': s.get('address', ''), 'Telephone': s.get('telephone', ''),
                    'KG': s['kg'], 'Latitude': s['coords'][0], 'Longitude': s['coords'][1]
                } for s in seq_list])
                draft_df.insert(0, 'Σειρά', range(1, len(draft_df) + 1))
                st.session_state.draft_sequence = draft_df

                for i in range(len(seq_list)):
                    _, _, d_min = get_osrm_data([pts[i], pts[i+1]])
                    seq_list[i]['drive_to'] = d_min
                st.session_state.route_data = seq_list
                geom, _, _ = get_osrm_data(pts)
                st.session_state.route_geom = geom
                st.rerun()

            if st.session_state.draft_sequence is not None:
                st.info("💡 **Διπλό κλικ στη στήλη 'Σειρά'** για αλλαγή της σειράς.")
                edited_seq = st.data_editor(st.session_state.draft_sequence, hide_index=True, use_container_width=True, disabled=['Name', 'Address', 'Telephone', 'KG', 'Latitude', 'Longitude'])
                
                if st.button("2. Εφαρμογή & Υπολογισμός", type="primary", use_container_width=True):
                    edited_seq = edited_seq.sort_values(by='Σειρά')
                    pts, final_seq = [curr_loc], []
                    for _, row in edited_seq.iterrows():
                        pts.append((row['Latitude'], row['Longitude']))
                        un_time = (row['KG'] / 1000) * 10
                        final_seq.append({
                            'name': row['Name'], 'address': row['Address'], 'telephone': row.get('Telephone', ''),
                            'kg': row['KG'], 'unload': un_time, 'coords': (row['Latitude'], row['Longitude'])
                        })
                    
                    for i in range(len(final_seq)):
                        _, _, d_min = get_osrm_data([pts[i], pts[i+1]])
                        final_seq[i]['drive_to'] = d_min
                    
                    st.session_state.route_data = final_seq
                    geom, _, _ = get_osrm_data(pts)
                    st.session_state.route_geom = geom
                    st.rerun()

            if st.session_state.route_data:
                st.divider()
                st.write("**Τελικό Δρομολόγιο:**")
                for i, s in enumerate(st.session_state.route_data):
                    st.write(f"**{i+1}. {s['name']}** ({s.get('address', '')}): 🚛 ~{int(s.get('drive_to', 0))}' | 🏗️ ~{int(s.get('unload', 0))}' (Φορτίο: {gr_num(s['kg'], 0)} KG)")
                
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                folium.Marker(curr_loc, popup="Αφετηρία", icon=folium.Icon(color='green', icon='play')).add_to(m2)

                if st.session_state.route_geom:
                    folium.PolyLine([[l, lon] for lon, l in st.session_state.route_geom], color="#007bff", weight=5).add_to(m2)
                    for i, s in enumerate(st.session_state.route_data):
                        seq_num = i + 1
                        
                        phone_raw = str(s.get('telephone', ''))
                        tel_html = f"<br><br><a href='tel:{''.join(c for c in phone_raw if c.isdigit() or c == '+')}' style='background-color:#28a745; color:white; padding:6px 12px; text-decoration:none; border-radius:5px; display:inline-block; font-weight:bold;'>📞 Κλήση: {phone_raw}</a>" if phone_raw and phone_raw.lower() not in ['nan', 'none', ''] else ""
                        kg_info = f"<br>📦 Φορτίο: {gr_num(s.get('kg', 0), 1)} KG"
                        popup_content = f"<b>Στάση {seq_num}: {s['name']}</b><br>{s.get('address', '')}{kg_info}{tel_html}"
                        
                        pin_html = f'''<div style="background-color:#E3000F; color:white; border-radius:50%; width:28px; height:28px; display:flex; justify-content:center; align-items:center; font-weight:bold; border:2px solid white; box-shadow: 0px 2px 4px rgba(0,0,0,0.4); font-size:13px;">{seq_num}</div>'''
                        folium.Marker(
                            [s['coords'][0], s['coords'][1]], 
                            popup=folium.Popup(popup_content, max_width=300), 
                            tooltip=f"{seq_num}. {s['name']}", 
                            icon=folium.DivIcon(html=pin_html, icon_size=(28, 28), icon_anchor=(14, 14))
                        ).add_to(m2)
                
                st_folium(m2, width="100%", height=450, key=f"routing_map_{hash(str(st.session_state.route_data))}")

        with tab3:
            st.subheader("POD Protocol")
            custList = [s['name'] for s in st.session_state.route_data] if st.session_state.route_data else sorted(user_data['Name'].unique())
            active_cust = st.selectbox("Πελάτης", custList)
            cust_rows = user_data[user_data['Name'] == active_cust]
            use_cam = st.checkbox("Ενεργοποίηση Κάμερας")
            photo = st.camera_input("📸 Φωτογραφία") if use_cam else None
            
            c1, c2 = st.columns(2)
            if c1.button("▶️ Άφιξη", use_container_width=True):
                st.session_state.start_time = datetime.now(GR_TIME)
                st.success("Η ώρα άφιξης καταγράφηκε.")
            if c2.button("⏹️ Sync POD", type="primary", use_container_width=True):
                if st.session_state.start_time:
                    dur = (datetime.now(GR_TIME) - st.session_state.start_time).total_seconds() / 60
                    p_kg = cust_rows[['Unpainted', 'White', 'Colored']].sum().sum()
                    a_kg = cust_rows['Accessories'].sum()
                    new_log = pd.DataFrame([{
                        "Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'), 
                        "Driver": st.session_state.username, "Plate": st.session_state.display_plate, 
                        "Customer": active_cust, "Profiles_KG": p_kg, "Accessories_KG": a_kg,
                        "Unload_Mins": round(dur, 1), "Photo": "Yes" if photo else "No"
                    }])
                    conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log], ignore_index=True))
                    st.success("Το POD συγχρονίστηκε επιτυχώς!")
                    st.session_state.start_time = None
                else: st.error("Πατήστε 'Άφιξη' πρώτα!")

        with tab4:
            tot = user_data['Total KG'].sum()
            prof = user_data[['Unpainted', 'White', 'Colored']].sum().sum()
            acc = user_data['Accessories'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Σύνολο", f"{gr_num(tot, 1)} KG")
            c2.metric("Προφίλ", f"{gr_num(prof, 1)} KG")
            c3.metric("Εξαρτήματα", f"{gr_num(acc, 1)} KG")
            st.progress(min(tot/24000, 1.0))
            st.caption(f"Load Factor: {gr_num((tot/24000)*100, 1)}%")

        with tab5:
            st.subheader("📩 Ειδοποίηση Επόμενου Πελάτη")
            if st.session_state.route_data:
                names = [s['name'] for s in st.session_state.route_data]
                if active_cust in names:
                    idx = names.index(active_cust)
                    if idx < len(st.session_state.route_data) - 1:
                        nxt_cust = st.session_state.route_data[idx+1]
                        nxt_name = nxt_cust['name']
                        
                        nxt_rows = user_data[user_data['Name'] == nxt_name]
                        nxt_prof = nxt_rows[['Unpainted', 'White', 'Colored']].sum().sum()
                        nxt_acc = nxt_rows['Accessories'].sum()
                        nxt_tot = nxt_cust.get('kg', 0)
                        
                        curr_unload = next(s.get('unload', 0) for s in st.session_state.route_data if s['name'] == active_cust)
                        total_wait = int(curr_unload + nxt_cust.get('drive_to', 0))
                        
                        subject = f"Αναμενόμενη Παράδοση Alumil - {nxt_name}"
                        body_ui = f"""Αγαπητέ συνεργάτη ({nxt_name}),\n\nΗ εκφόρτωση στον προηγούμενο σταθμό βρίσκεται σε εξέλιξη. Η εκτιμώμενη άφιξη στις εγκαταστάσεις σας είναι σε περίπου **{total_wait} λεπτά**.\n\n📦 **Στοιχεία Παράδοσης:**\n* Προφίλ: {gr_num(nxt_prof, 1)} KG\n* Εξαρτήματα: {gr_num(nxt_acc, 1)} KG\n* **Σύνολο: {gr_num(nxt_tot, 1)} KG**\n\n🚚 Όχημα: {st.session_state.display_plate}"""
                        st.info(body_ui)
                        
                        body_mail = f"Αγαπητέ συνεργάτη ({nxt_name}),\n\nΗ εκφόρτωση στον προηγούμενο σταθμό βρίσκεται σε εξέλιξη. Η εκτιμώμενη άφιξη στις εγκαταστάσεις σας είναι σε περίπου {total_wait} λεπτά.\n\nΣτοιχεία Παράδοσης:\n- Προφίλ: {gr_num(nxt_prof, 1)} KG\n- Εξαρτήματα: {gr_num(nxt_acc, 1)} KG\n- Σύνολο: {gr_num(nxt_tot, 1)} KG\n\nΌχημα: {st.session_state.display_plate}\n\nΕυχαριστούμε για τη συνεργασία."
                        link = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body_mail)}"
                        st.markdown(f'<a href="{link}" target="_blank" style="padding:15px; background-color:#007bff; color:white; border-radius:8px; text-decoration:none;">📧 Email Ειδοποίησης στον επόμενο</a>', unsafe_allow_html=True)
                    else:
                        st.success("🏁 Αυτός είναι ο τελευταίος πελάτης.")
            else:
                st.warning("Υπολογίστε το δρομολόγιο στο Tab 2.")

# --- 2. ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Control Panel")
    logs = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.dataframe(logs.tail(20), use_container_width=True)

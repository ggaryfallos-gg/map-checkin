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
st.set_page_config(page_title="Alumil Logistics Hub v24", layout="wide")
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
    try: return float(str(v).replace('.', '').replace(',', '.'))
    except: return 0.0

def gr_num(val, decimals=1):
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

# --- DATA PIPELINE ---
@st.cache_data(ttl=300)
def load_full_data():
    # 1. Φόρτωση Shipments
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    ship['Delivery'] = ship['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False)
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    # 2. Φόρτωση Deliveries (ERP Join)
    try:
        dels = conn.read(spreadsheet=DELIVERIES_URL, ttl=300)
        dels['Delivery'] = dels['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False)
        dels_sub = dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery')
        ship = pd.merge(ship, dels_sub, on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη Ημ/νία').astype(str)
        # Καθαρισμός nan texts
        ship['Loading_Date'] = ship['Loading_Date'].replace('nan', 'Άγνωστη Ημ/νία')
    except:
        ship['Loading_Date'] = 'Άγνωστη Ημ/νία'

    # 3. Φόρτωση Cusaddress
    try:
        cus_df = conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
        for col in ['Name', 'Street', 'Telephone 1', 'Postal Code']:
            if col not in cus_df.columns: cus_df[col] = ''
        cus_df = cus_df[['Name', 'Street', 'Telephone 1', 'Postal Code']].drop_duplicates('Name')
        df = pd.merge(ship, cus_df, on='Name', how='left')
    except:
        df = ship.copy()
        df['Street'], df['Telephone 1'], df['Postal Code'] = '', '', ''

    # 4. Fallback Coords
    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords['City_Match'] = coords['City'].astype(str).str.strip().str.upper()
    coords = coords.rename(columns={'Latitude': 'Lat_city', 'Longitude': 'Lon_city'})
    df = pd.merge(df, coords.drop_duplicates('City_Match'), left_on='City_Clean', right_on='City_Match', how='left')
    
    # Ομαδοποίηση βάσει Πινακίδας ΚΑΙ Ημερομηνίας Φόρτωσης
    counts = df.groupby(['Plate_Clean', 'Loading_Date'])['Name'].nunique().reset_index(name='Dests')
    unique_routes = df[['Truck License Plate', 'Plate_Clean', 'Loading_Date']].drop_duplicates()
    fleet_summary = pd.merge(unique_routes, counts, on=['Plate_Clean', 'Loading_Date'], how='left').fillna(0)
    fleet_summary['Label'] = fleet_summary.apply(lambda r: f"{r['Truck License Plate']} | Φόρτ.: {r['Loading_Date']} ({int(r['Dests'])} Στάσεις)", axis=1)
    
    return fleet_summary.sort_values('Dests', ascending=False), df

fleet_info, all_data = load_full_data()

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
st.sidebar.write(f"👤 **{st.session_state.username}**")

if st.session_state.user_plate is not None:
    st.sidebar.divider()
    if st.sidebar.button("🔄 Αλλαγή Οχήματος", use_container_width=True):
        st.session_state.user_plate = None
        st.session_state.loading_date = None
        st.session_state.draft_sequence = None
        st.session_state.route_data = []
        st.session_state.route_geom = None
        st.session_state.start_time = None
        st.rerun()

st.sidebar.divider()
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

if st.sidebar.button("🚪 Logout"):
    st.session_state.password_correct = False
    st.session_state.user_plate = None
    st.session_state.loading_date = None
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        sel = st.selectbox("Επιλέξτε Όχημα & Φόρτωση", fleet_info['Label'])
        if st.button("Έναρξη", type="primary", use_container_width=True):
            row = fleet_info[fleet_info['Label'] == sel].iloc[0]
            st.session_state.user_plate = row['Plate_Clean']
            st.session_state.loading_date = row['Loading_Date']
            st.session_state.display_plate = row['Truck License Plate']
            st.rerun()
    else:
        st.subheader(f"🚚 {st.session_state.display_plate} (Φόρτωση: {st.session_state.loading_date})")
        
        # Απομόνωση δεδομένων χρήστη βάσει Πινακίδας ΚΑΙ Ημερομηνίας
        user_data = all_data[(all_data['Plate_Clean'] == st.session_state.user_plate) & (all_data['Loading_Date'] == st.session_state.loading_date)].copy()
        
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        # Δυναμικό Geocoding & Fallback
        if 'Final_Lat' not in user_data.columns:
            user_data['Final_Lat'] = user_data['Lat_city']
            user_data['Final_Lon'] = user_data['Lon_city']
            user_data['Display_Address'] = user_data['City_x']
            
            for idx, row in user_data.iterrows():
                street = str(row.get('Street', ''))
                city = str(row.get('City_x', ''))
                if street and street.lower() not in ['nan', 'none']:
                    user_data.at[idx, 'Display_Address'] = f"{street}, {city}"
                    lat, lon = geocode_address(street, city)
                    if lat and lon:
                        user_data.at[idx, 'Final_Lat'] = lat
                        user_data.at[idx, 'Final_Lon'] = lon

        tab1, tab2, tab3, tab4, tab5 = st.tabs(["🌎 Γενικός Χάρτης", "🛣️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Ειδοποίηση"])

        with tab1:
            st.write("Σημεία εκφόρτωσης (Ακριβείς Διευθύνσεις):")
            m1 = folium.Map(location=curr_loc, zoom_start=7)
            folium.Marker(curr_loc, popup="Η θέση μου", icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m1)
            for _, r in user_data.drop_duplicates(subset=['Name']).iterrows():
                if pd.notna(r['Final_Lat']):
                    tel_info = f"<br>📞 {r['Telephone 1']}" if str(r.get('Telephone 1', '')) not in ['nan', '', 'None'] else ""
                    popup_html = f"<b>{r['Name']}</b><br>{r['Display_Address']}{tel_info}"
                    folium.Marker([r['Final_Lat'], r['Final_Lon']], popup=popup_html, tooltip=f"{r['Name']}").add_to(m1)
            st_folium(m1, width="100%", height=500, key="all_points_map")

        with tab2:
            st.subheader("Σχεδιασμός & Βελτιστοποίηση (Με Ad-Hoc)")
            
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
                    seq_list.append({'name': row['Name'], 'address': row['Display_Address'], 'kg': row['Total KG'], 'unload': un_time, 'coords': (row['Final_Lat'], row['Final_Lon'])})
                    unvisited = unvisited.drop(index=idx)
                
                draft_df = pd.DataFrame([{'Name': s['name'], 'Address': s['address'], 'KG': s['kg'], 'Latitude': s['coords'][0], 'Longitude': s['coords'][1]} for s in seq_list])
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
                st.info("💡 **Διπλό κλικ στη στήλη 'Σειρά'** για να αλλάξετε τη σειρά χειροκίνητα.")
                edited_seq = st.data_editor(st.session_state.draft_sequence, hide_index=True, use_container_width=True, disabled=['Name', 'Address', 'KG', 'Latitude', 'Longitude'])
                
                if st.button("2. Εφαρμογή Νέας Σειράς & Χάρτη", type="primary", use_container_width=True):
                    edited_seq = edited_seq.sort_values(by='Σειρά')
                    pts, final_seq = [curr_loc], []
                    for _, row in edited_seq.iterrows():
                        pts.append((row['Latitude'], row['Longitude']))
                        un_time = (row['KG'] / 1000) * 10
                        final_seq.append({'name': row['Name'], 'address': row['Address'], 'kg': row['KG'], 'unload': un_time, 'coords': (row['Latitude'], row['Longitude'])})
                    
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
                    st.write(f"**{i+1}. {s['name']}** ({s['address']}): 🚛 ~{int(s['drive_to'])}' | 🏗️ ~{int(s['unload'])}'")
                
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                folium.Marker(curr_loc, popup="Αφετηρία", icon=folium.Icon(color='green', icon='play')).add_to(m2)

                if st.session_state.route_geom:
                    folium.PolyLine([[l, lon] for lon, l in st.session_state.route_geom], color="#007bff", weight=5).add_to(m2)
                    for i, s in enumerate(st.session_state.route_data):
                        seq_num = i + 1
                        pin_html = f'''<div style="background-color:#E3000F; color:white; border-radius:50%; width:28px; height:28px; display:flex; justify-content:center; align-items:center; font-weight:bold; border:2px solid white; box-shadow: 0px 2px 4px rgba(0,0,0,0.4); font-size:13px;">{seq_num}</div>'''
                        folium.Marker([s['coords'][0], s['coords'][1]], popup=f"Στάση {seq_num}: {s['name']}", tooltip=f"{seq_num}. {s['name']}", icon=folium.DivIcon(html=pin_html, icon_size=(28, 28), icon_anchor=(14, 14))).add_to(m2)
                
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
                        nxt_tot = nxt_cust['kg']
                        
                        curr_unload = next(s['unload'] for s in st.session_state.route_data if s['name'] == active_cust)
                        total_wait = int(curr_unload + nxt_cust['drive_to'])
                        
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

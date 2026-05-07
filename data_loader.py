import streamlit as st
import pandas as pd
from utils import clean_val

# --- DATA PIPELINE ---
@st.cache_data(ttl=300, show_spinner="Φόρτωση δεδομένων από Google Sheets...")
def load_full_data(_conn, SHIPMENTS_URL, DELIVERIES_URL, CUSADDRESS_URL, COORDS_URL):
    ship = _conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship.columns = ship.columns.str.strip()
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    ship['Delivery'] = ship['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
    
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    try:
        dels = _conn.read(spreadsheet=DELIVERIES_URL, ttl=300)
        dels.columns = dels.columns.str.strip()
        dels['Delivery'] = dels['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
        dels_sub = dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery')
        ship = pd.merge(ship, dels_sub, on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη Ημ/νία').astype(str)
        ship['Loading_Date'] = ship['Loading_Date'].replace(['nan', 'NaT', 'None', ''], 'Άγνωστη Ημ/νία')
    except:
        ship['Loading_Date'] = 'Άγνωστη Ημ/νία'

    try:
        cus_df = _conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
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

    coords = read(spreadsheet=COORDS_URL, ttl=300)
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

# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import datetime
import json
import time
import os
import xml.etree.ElementTree as ET # For robust XML parsing

app = Flask(__name__)

# --- Configuration ---
# USGS Earthquake API endpoint for recent earthquakes (global coverage)
USGS_EARTHQUAKE_API = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# NWS Alerts API endpoint for active alerts (covers US and territories, includes tsunami)
NWS_ALERTS_API = "https://api.weather.gov/alerts/active"

# Smithsonian Global Volcanism Program (GVP) API for recent activity (XML Feed)
GVP_WEEKLY_REPORT_FEED = "https://volcano.si.edu/feeds/WeeklyVolcanicActivityReport.xml"

# GDACS (Global Disaster Alert and Coordination System) GeoRSS Feed (XML)
# This feed provides alerts for Earthquakes, Tropical Cyclones, Floods, Volcanoes, Wildfires, Droughts
GDACS_ALERTS_FEED = "https://www.gdacs.org/rss.aspx?profile=ARCHIVE&fromarchive=true"

# NASA EONET (Earth Observatory Natural Event Tracker) API for events like Wildfires
EONET_API = "https://eonet.gsfc.nasa.gov/api/v3/events"

# User-Agent header is required by NWS API and good practice for others
# IMPORTANT: Replace with your actual Twitch channel name and a contact email.
NWS_HEADERS = {
    "User-Agent": "(HammerDln, weatherbot.scowling782@passmail.net)"
}

# --- In-memory cache for last posted alerts and cooldowns ---
# In a production environment, for persistence across restarts or multiple instances,
# consider using a database (e.g., Redis, Firestore) for this state.
alert_state = {
    "last_earthquake_id": None,
    "last_earthquake_post_time": 0,
    "last_tsunami_id": None,
    "last_tsunami_post_time": 0,
    "last_volcano_event": None,
    "last_volcano_post_time": 0,
    "last_flood_id": None,
    "last_flood_post_time": 0,
    "last_tropical_cyclone_id": None,
    "last_tropical_cyclone_post_time": 0,
    "last_wildfire_id": None,
    "last_wildfire_post_time": 0,
    "last_drought_id": None,
    "last_drought_post_time": 0,
    "last_general_severe_weather_id": None,
    "last_general_severe_weather_post_time": 0,
}

# Cooldown period in seconds to prevent spamming chat
COOLDOWN_SECONDS = 300 # 5 minutes

# --- Helper Functions for API Calls with Exponential Backoff ---
def fetch_data_with_backoff(url, headers=None, params=None, max_retries=5, parser='json'):
    """
    Fetches data from a given URL with exponential backoff.
    Can parse JSON or XML.
    """
    retries = 0
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            if parser == 'json':
                return response.json()
            elif parser == 'xml':
                return response.text # Return raw XML for parsing
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {url}: {e}")
            retries += 1
            sleep_time = 2 ** retries # Exponential backoff
            print(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
    print(f"Failed to fetch {url} after {max_retries} retries.")
    return None

# --- Earthquake Alert Endpoint (USGS Global) ---
@app.route('/earthquake')
def get_earthquake_alert():
    """
    Fetches the latest significant earthquake (global) and formats a message for Twitch chat,
    applying a cooldown and only posting new alerts.
    """
    current_time = time.time()
    now_utc = datetime.datetime.utcnow()
    one_hour_ago_utc = now_utc - datetime.timedelta(hours=1)
    
    params = {
        "format": "geojson",
        "orderby": "time",
        "limit": 1,
        "minmagnitude": 4.0, # Minimum magnitude to report (global)
        "starttime": one_hour_ago_utc.isoformat(timespec='seconds') + 'Z'
    }

    data = fetch_data_with_backoff(USGS_EARTHQUAKE_API, params=params)

    if data and data['features']:
        latest_earthquake = data['features'][0]['properties']
        latest_earthquake_id = data['features'][0]['id']

        if latest_earthquake_id != alert_state["last_earthquake_id"] and \
           (current_time - alert_state["last_earthquake_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_earthquake_id"] = latest_earthquake_id
            alert_state["last_earthquake_post_time"] = current_time

            magnitude = latest_earthquake.get('mag')
            place = latest_earthquake.get('place', 'Unknown location')
            time_ms = latest_earthquake.get('time')
            detail_url = latest_earthquake.get('url')

            if time_ms:
                dt_object = datetime.datetime.fromtimestamp(time_ms / 1000, tz=datetime.timezone.utc)
                event_time = dt_object.strftime('%Y-%m-%d %H:%M:%S UTC')
            else:
                event_time = "Unknown time"

            message = (
                f"🚨 EARTHQUAKE ALERT! 🚨 Magnitude {magnitude:.1f} - {place} "
                f"at {event_time}. More info: {detail_url}"
            )
            return message
    return ""

# --- Tsunami Alert Endpoint (NWS for US/Territories) ---
@app.route('/tsunami')
def get_tsunami_alert():
    """
    Fetches active tsunami warnings/advisories (US/Territories) and formats a message.
    """
    current_time = time.time()
    params = {
        "event": "Tsunami Warning,Tsunami Advisory"
    }

    data = fetch_data_with_backoff(NWS_ALERTS_API, headers=NWS_HEADERS, params=params)

    if data and data['features']:
        severity_order = {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}
        active_alerts = sorted(
            data['features'],
            key=lambda x: severity_order.get(x['properties'].get('severity', 'Unknown'), 0),
            reverse=True
        )

        if active_alerts:
            latest_alert = active_alerts[0]['properties']
            latest_alert_id = latest_alert.get('id')

            if latest_alert_id != alert_state["last_tsunami_id"] and \
               (current_time - alert_state["last_tsunami_post_time"]) > COOLDOWN_SECONDS:
                
                alert_state["last_tsunami_id"] = latest_alert_id
                alert_state["last_tsunami_post_time"] = current_time

                headline = latest_alert.get('headline', 'No Headline')
                description = latest_alert.get('description', 'No description provided.')
                instruction = latest_alert.get('instruction', 'No specific instructions. Follow local authority guidance.')
                web_link = latest_alert.get('web', 'No additional web link.')
                area_desc = latest_alert.get('areaDesc', 'General Area')

                if len(description) > 150: description = description[:147] + "..."
                if len(instruction) > 100: instruction = instruction[:97] + "..."

                message = (
                    f"🌊 TSUNAMI ALERT! 🌊 {headline} ({area_desc}). "
                    f"Details: {description}. Action: {instruction}. More info: {web_link}"
                )
                return message
    return ""

# --- Volcano Alert Endpoint (Smithsonian GVP) ---
@app.route('/volcano')
def get_volcano_alert():
    """
    Fetches the latest significant volcanic activity from GVP (global) and formats a message.
    Uses robust XML parsing.
    """
    current_time = time.time()
    xml_data = fetch_data_with_backoff(GVP_WEEKLY_REPORT_FEED, parser='xml')

    if xml_data:
        try:
            root = ET.fromstring(xml_data)
            # Find the first item (latest report)
            item = root.find('.//item')
            if item is not None:
                title = item.find('title').text if item.find('title') is not None else "Unknown Volcano"
                link = item.find('link').text if item.find('link') is not None else "No link"
                
                # Use title as unique ID for simplicity, or generate a hash of content
                latest_volcano_event_id = title 

                if latest_volcano_event_id != alert_state["last_volcano_event"] and \
                   (current_time - alert_state["last_volcano_post_time"]) > COOLDOWN_SECONDS:
                    
                    alert_state["last_volcano_event"] = latest_volcano_event_id
                    alert_state["last_volcano_post_time"] = current_time

                    message = (
                        f"🌋 VOLCANO ALERT! 🌋 Latest activity: {title}. "
                        f"More info: {link}"
                    )
                    return message
        except ET.ParseError as e:
            print(f"Error parsing GVP XML: {e}")
    return ""

# --- Helper to parse GDACS GeoRSS Feed ---
def get_gdacs_alerts(event_type_filter=None, alert_level_filter=None):
    """
    Fetches and parses GDACS GeoRSS feed for specific event types and alert levels.
    Returns a list of dictionaries with alert details.
    """
    xml_data = fetch_data_with_backoff(GDACS_ALERTS_FEED, parser='xml')
    alerts = []

    if xml_data:
        try:
            root = ET.fromstring(xml_data)
            # Namespace for GDACS elements
            gdacs_ns = {'gdacs': 'http://www.gdacs.org/schemas/gdacs/1.0'}
            
            for item in root.findall('.//item'):
                event_type_elem = item.find('gdacs:eventtype', gdacs_ns)
                alert_level_elem = item.find('gdacs:alertlevel', gdacs_ns)
                
                current_event_type = event_type_elem.text if event_type_elem is not None else None
                current_alert_level = alert_level_elem.text if alert_level_elem is not None else None

                # Filter by event type and alert level if specified
                if (event_type_filter is None or current_event_type == event_type_filter) and \
                   (alert_level_filter is None or current_alert_level == alert_level_filter):
                    
                    title = item.find('title').text if item.find('title') is not None else "Unknown Event"
                    link = item.find('link').text if item.find('link') is not None else "No link"
                    event_id = item.find('gdacs:eventid', gdacs_ns).text if item.find('gdacs:eventid', gdacs_ns) is not None else title # Fallback ID
                    description = item.find('description').text if item.find('description') is not None else ""

                    alerts.append({
                        "id": event_id,
                        "title": title,
                        "link": link,
                        "event_type": current_event_type,
                        "alert_level": current_alert_level,
                        "description": description
                    })
            # Sort by alert level (Red > Orange > Green)
            level_priority = {"Red": 3, "Orange": 2, "Green": 1}
            alerts.sort(key=lambda x: level_priority.get(x.get('alert_level', 'Green'), 0), reverse=True)
        except ET.ParseError as e:
            print(f"Error parsing GDACS XML: {e}")
    return alerts

# --- Flooding Alert Endpoint (GDACS International) ---
@app.route('/flood')
def get_flood_alert():
    """
    Fetches the latest significant flood alert (GDACS International) and formats a message.
    """
    current_time = time.time()
    alerts = get_gdacs_alerts(event_type_filter="FL", alert_level_filter="Orange") # Or "Red" for most severe

    if alerts:
        latest_flood = alerts[0]
        latest_flood_id = latest_flood['id']

        if latest_flood_id != alert_state["last_flood_id"] and \
           (current_time - alert_state["last_flood_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_flood_id"] = latest_flood_id
            alert_state["last_flood_post_time"] = current_time

            message = (
                f"⚠️ FLOOD ALERT! ⚠️ {latest_flood['alert_level']} alert for {latest_flood['title']}. "
                f"More info: {latest_flood['link']}"
            )
            return message
    return ""

# --- Tropical Cyclone Alert Endpoint (GDACS International) ---
@app.route('/tropical_cyclone')
def get_tropical_cyclone_alert():
    """
    Fetches the latest significant tropical cyclone alert (GDACS International) and formats a message.
    """
    current_time = time.time()
    alerts = get_gdacs_alerts(event_type_filter="TC", alert_level_filter="Orange") # Or "Red"

    if alerts:
        latest_tc = alerts[0]
        latest_tc_id = latest_tc['id']

        if latest_tc_id != alert_state["last_tropical_cyclone_id"] and \
           (current_time - alert_state["last_tropical_cyclone_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_tropical_cyclone_id"] = latest_tc_id
            alert_state["last_tropical_cyclone_post_time"] = current_time

            message = (
                f"🌀 TROPICAL CYCLONE ALERT! 🌀 {latest_tc['alert_level']} alert for {latest_tc['title']}. "
                f"More info: {latest_tc['link']}"
            )
            return message
    return ""

# --- Wildfire Alert Endpoint (NASA EONET) ---
@app.route('/wildfire')
def get_wildfire_alert():
    """
    Fetches the latest open wildfire event (NASA EONET) and formats a message.
    """
    current_time = time.time()
    params = {
        "status": "open",
        "category": "wildfires",
        "limit": 1,
        "days": 7 # Look for events in the last 7 days
    }
    data = fetch_data_with_backoff(EONET_API, params=params)

    if data and data.get('events'):
        latest_wildfire = data['events'][0]
        latest_wildfire_id = latest_wildfire.get('id')

        if latest_wildfire_id != alert_state["last_wildfire_id"] and \
           (current_time - alert_state["last_wildfire_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_wildfire_id"] = latest_wildfire_id
            alert_state["last_wildfire_post_time"] = current_time

            title = latest_wildfire.get('title', 'Unknown Wildfire')
            link = latest_wildfire.get('link', 'No link')
            
            message = (
                f"🔥 WILDFIRE ALERT! 🔥 Active wildfire: {title}. "
                f"More info: {link}"
            )
            return message
    return ""

# --- Drought Alert Endpoint (GDACS International) ---
@app.route('/drought')
def get_drought_alert():
    """
    Fetches the latest significant drought alert (GDACS International) and formats a message.
    """
    current_time = time.time()
    alerts = get_gdacs_alerts(event_type_filter="DR", alert_level_filter="Orange") # Or "Red"

    if alerts:
        latest_drought = alerts[0]
        latest_drought_id = latest_drought['id']

        if latest_drought_id != alert_state["last_drought_id"] and \
           (current_time - alert_state["last_drought_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_drought_id"] = latest_drought_id
            alert_state["last_drought_post_time"] = current_time

            message = (
                f"🏜️ DROUGHT ALERT! 🏜️ {latest_drought['alert_level']} alert for {latest_drought['title']}. "
                f"More info: {latest_drought['link']}"
            )
            return message
    return ""

# --- General Severe Weather Alert Endpoint (Leveraging GDACS for broad categories) ---
@app.route('/severe_weather_general')
def get_general_severe_weather_alert():
    """
    Provides a general severe weather alert, primarily leveraging GDACS for broader categories
    like severe storms (which might encompass some aspects of tornadoes, blizzards, heatwaves
    if they are part of a larger, high-impact event GDACS tracks).
    
    Note: For highly localized warnings (e.g., specific tornado warnings, flash flood warnings)
    outside the US, a dedicated commercial API or parsing of national weather service feeds
    would be required. This endpoint focuses on what GDACS can provide.
    """
    current_time = time.time()
    
    # GDACS doesn't have a specific "Tornado" or "Blizzard" event type
    # but "Tropical Cyclone" and "Flood" are covered.
    # EONET has a "severeStorms" category, but GDACS is better for "alerts".
    
    # We'll check GDACS for any Orange/Red alerts that aren't already covered by specific endpoints
    # (EQ, TS, FL, TC, VO, WF, DR are handled by their own endpoints)
    
    # Get all Orange/Red alerts from GDACS
    all_gdacs_alerts = get_gdacs_alerts(alert_level_filter="Orange") + get_gdacs_alerts(alert_level_filter="Red")
    
    # Filter out alerts already handled by dedicated endpoints (EQ, TS, FL, TC, VO, WF, DR)
    # Note: GDACS also reports EQ, TC, FL, VO, WF, DR, so we need to avoid duplicates
    # if those alerts are also picked up by other specific APIs.
    # For simplicity here, we'll just look for any *new* high-level GDACS alerts.
    
    if all_gdacs_alerts:
        # Prioritize Red alerts over Orange
        all_gdacs_alerts.sort(key=lambda x: {"Red": 2, "Orange": 1}.get(x['alert_level'], 0), reverse=True)
        
        latest_alert = all_gdacs_alerts[0]
        # Create a unique ID for this general alert based on its type, level, and GDACS ID
        latest_general_alert_id = f"{latest_alert['event_type']}-{latest_alert['alert_level']}-{latest_alert['id']}"

        if latest_general_alert_id != alert_state["last_general_severe_weather_id"] and \
           (current_time - alert_state["last_general_severe_weather_post_time"]) > COOLDOWN_SECONDS:
            
            alert_state["last_general_severe_weather_id"] = latest_general_alert_id
            alert_state["last_general_severe_weather_post_time"] = current_time

            message = (
                f"⚠️ SEVERE WEATHER ALERT! ⚠️ {latest_alert['alert_level']} alert for {latest_alert['event_type']}: "
                f"{latest_alert['title']}. More info: {latest_alert['link']}"
            )
            return message
    
    return ""

# --- Root Endpoint (for testing the proxy itself) ---
@app.route('/')
def index():
    return "Natural Disaster Chatbot Proxy is running. Endpoints: /earthquake, /tsunami, /volcano, /flood, /tropical_cyclone, /wildfire, /drought, /severe_weather_general."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)

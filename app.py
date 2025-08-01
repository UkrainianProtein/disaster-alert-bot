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
GVP_WEEKLY_REPORT_FEED = "https://volcano.si.edu/news/weekly_report.cfm?xml=true"

# GDACS (Global Disaster Alert and Coordination System) GeoRSS Feed (XML)
GDACS_ALERTS_FEED = "https://www.gdacs.org/rss.aspx?profile=ARCHIVE&fromarchive=true"

# NASA EONET (Earth Observatory Natural Event Tracker) API for events like Wildfires
EONET_API = "https://eonet.gsfc.nasa.gov/api/v3/events"

# Pacific Tsunami Warning Center (PTWC) Atom Feed for global tsunami alerts
PTWC_ATOM_FEED = "https://www.tsunami.gov/events/xml/PHEBAtom.xml"

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
    "last_tsunami_id": None, # This will now track either NWS or PTWC ID
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
    "last_all_alerts_id": None, 
    "last_all_alerts_post_time": 0,
}

# Cooldown period in seconds to prevent spamming chat for automated timers
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
def _get_earthquake_alert_message():
    """Internal function to get earthquake alert message without cooldown logic."""
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
        magnitude = latest_earthquake.get('mag')
        place = latest_earthquake.get('place', 'Unknown location')
        time_ms = latest_earthquake.get('time')
        detail_url = latest_earthquake.get('url')
        latest_earthquake_id = data['features'][0]['id']

        if time_ms:
            dt_object = datetime.datetime.fromtimestamp(time_ms / 1000, tz=datetime.timezone.utc)
            event_time = dt_object.strftime('%Y-%m-%d %H:%M:%S UTC')
        else:
            event_time = "Unknown time"

        message = (
            f"🚨 EARTHQUAKE ALERT! 🚨 Magnitude {magnitude:.1f} - {place} "
            f"at {event_time}. More info: {detail_url}"
        )
        return message, latest_earthquake_id
    return "", None

@app.route('/earthquake')
def get_earthquake_alert():
    current_time = time.time()
    message, event_id = _get_earthquake_alert_message()
    if message and event_id != alert_state["last_earthquake_id"] and \
       (current_time - alert_state["last_earthquake_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_earthquake_id"] = event_id
        alert_state["last_earthquake_post_time"] = current_time
        return message
    return ""

# --- Tsunami Alert Endpoint (NWS for US/Territories + PTWC Global) ---
def _get_tsunami_alert_message():
    """
    Internal function to get tsunami alert message without cooldown logic.
    Prioritizes NWS alerts, then checks PTWC global alerts.
    """
    # 1. Check NWS Alerts (US and territories)
    params_nws = {
        "event": "Tsunami Warning,Tsunami Advisory"
    }
    data_nws = fetch_data_with_backoff(NWS_ALERTS_API, headers=NWS_HEADERS, params=params_nws)

    if data_nws and data_nws['features']:
        severity_order = {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}
        active_alerts_nws = sorted(
            data_nws['features'],
            key=lambda x: severity_order.get(x['properties'].get('severity', 'Unknown'), 0),
            reverse=True
        )

        if active_alerts_nws:
            latest_alert = active_alerts_nws[0]['properties']
            latest_alert_id = latest_alert.get('id')

            headline = str(latest_alert.get('headline', 'No Headline'))
            description = str(latest_alert.get('description', ''))
            instruction = str(latest_alert.get('instruction', ''))
            web_link = str(latest_alert.get('web', 'No additional web link.'))
            area_desc = str(latest_alert.get('areaDesc', 'General Area'))

            if len(description) > 150: description = description[:147] + "..."
            if len(instruction) > 100: instruction = instruction[:97] + "..."

            message = (
                f"🌊 TSUNAMI ALERT! (US/Territories) 🌊 {headline} ({area_desc}). "
                f"Details: {description}. Action: {instruction}. More info: {web_link}"
            )
            return message, latest_alert_id

    # 2. If no NWS alert, check PTWC Global Atom Feed
    xml_data_ptwc = fetch_data_with_backoff(PTWC_ATOM_FEED, parser='xml')

    if xml_data_ptwc:
        try:
            root = ET.fromstring(xml_data_ptwc)
            atom_ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            # Find the latest entry
            entry = root.find('atom:entry', atom_ns)
            if entry is not None:
                title_elem = entry.find('atom:title', atom_ns)
                link_elem = entry.find('atom:link', atom_ns)
                id_elem = entry.find('atom:id', atom_ns)
                summary_elem = entry.find('atom:summary', atom_ns)
                updated_elem = entry.find('atom:updated', atom_ns)

                title = str(title_elem.text) if title_elem is not None else "Unknown Global Tsunami Event"
                link = str(link_elem.get('href')) if link_elem is not None else "No link"
                event_id = str(id_elem.text) if id_elem is not None else title # Fallback ID
                summary = str(summary_elem.text) if summary_elem is not None else "No summary provided."
                
                # Format updated time if available
                updated_time = ""
                if updated_elem is not None and updated_elem.text:
                    try:
                        dt_object = datetime.datetime.fromisoformat(updated_elem.text.replace('Z', '+00:00'))
                        updated_time = dt_object.strftime('%Y-%m-%d %H:%M:%S UTC')
                    except ValueError:
                        pass # Keep empty if parsing fails

                if len(summary) > 200: summary = summary[:197] + "..."

                message = (
                    f"🌊 GLOBAL TSUNAMI ALERT! 🌊 {title}. "
                    f"Summary: {summary}. Last updated: {updated_time}. More info: {link}"
                )
                return message, event_id
        except ET.ParseError as e:
            print(f"Error parsing PTWC Atom XML: {e}")
        except AttributeError as e:
            print(f"Error accessing XML element in PTWC feed: {e}. XML structure might have changed.")
    
    return "", None

@app.route('/tsunami')
def get_tsunami_alert():
    current_time = time.time()
    message, event_id = _get_tsunami_alert_message()
    if message and event_id != alert_state["last_tsunami_id"] and \
       (current_time - alert_state["last_tsunami_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_tsunami_id"] = event_id
        alert_state["last_tsunami_post_time"] = current_time
        return message
    return ""

# --- Volcano Alert Endpoint (Smithsonian GVP) ---
def _get_volcano_alert_message():
    """Internal function to get volcano alert message without cooldown logic."""
    xml_data = fetch_data_with_backoff(GVP_WEEKLY_REPORT_FEED, parser='xml')

    if xml_data:
        try:
            root = ET.fromstring(xml_data)
            item = root.find('.//item') 
            
            if item is not None:
                title = str(item.find('title').text) if item.find('title') is not None else "Unknown Volcano"
                link = str(item.find('link').text) if item.find('link') is not None else "No link"
                
                latest_volcano_event_id = title 
                message = (
                    f"🌋 VOLCANO ALERT! 🌋 Latest activity: {title}. "
                    f"More info: {link}"
                )
                return message, latest_volcano_event_id
        except ET.ParseError as e:
            print(f"Error parsing GVP XML: {e}")
        except AttributeError as e:
            print(f"Error accessing XML element in GVP feed: {e}. XML structure might have changed.")
    return "", None

@app.route('/volcano')
def get_volcano_alert():
    current_time = time.time()
    message, event_id = _get_volcano_alert_message()
    if message and event_id != alert_state["last_volcano_event"] and \
       (current_time - alert_state["last_volcano_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_volcano_event"] = event_id
        alert_state["last_volcano_post_time"] = current_time
        return message
    return ""

# --- Helper to parse GDACS GeoRSS Feed ---
def _get_gdacs_alerts_data(event_type_filter=None, alert_level_filter=None):
    """
    Fetches and parses GDACS GeoRSS feed for specific event types and alert levels.
    Returns a list of dictionaries with alert details.
    """
    xml_data = fetch_data_with_backoff(GDACS_ALERTS_FEED, parser='xml')
    alerts = []

    if xml_data:
        try:
            root = ET.fromstring(xml_data)
            gdacs_ns = {'gdacs': 'http://www.gdacs.org/schemas/gdacs/1.0'}
            
            for item in root.findall('.//item'):
                event_type_elem = item.find('gdacs:eventtype', gdacs_ns)
                alert_level_elem = item.find('gdacs:alertlevel', gdacs_ns)
                
                current_event_type = str(event_type_elem.text) if event_type_elem is not None else None
                current_alert_level = str(alert_level_elem.text) if alert_level_elem is not None else None

                if (event_type_filter is None or current_event_type == event_type_filter) and \
                   (alert_level_filter is None or current_alert_level == alert_level_filter):
                    
                    title = str(item.find('title').text) if item.find('title') is not None else "Unknown Event"
                    link = str(item.find('link').text) if item.find('link') is not None else "No link"
                    event_id = str(item.find('gdacs:eventid', gdacs_ns).text) if item.find('gdacs:eventid', gdacs_ns) is not None else title 
                    description = str(item.find('description').text) if item.find('description') is not None else ""
                    
                    # Extract severity value for sorting
                    severity_value = 0
                    severity_elem = item.find('gdacs:severity', gdacs_ns)
                    if severity_elem is not None:
                        value_elem = severity_elem.find('gdacs:value', gdacs_ns)
                        if value_elem is not None and value_elem.text:
                            try:
                                severity_value = float(value_elem.text)
                            except ValueError:
                                pass # Keep 0 if not a valid number

                    alerts.append({
                        "id": event_id,
                        "title": title,
                        "link": link,
                        "event_type": current_event_type,
                        "alert_level": current_alert_level,
                        "description": description,
                        "severity_value": severity_value # Add severity for better sorting
                    })
            # Sort by alert level (Red > Orange > Green) and then by severity value
            level_priority = {"Red": 3, "Orange": 2, "Green": 1}
            alerts.sort(key=lambda x: (level_priority.get(x.get('alert_level', 'Green'), 0), x.get('severity_value', 0)), reverse=True)
        except ET.ParseError as e:
            print(f"Error parsing GDACS XML: {e}")
    return alerts

def _get_gdacs_alert_message(event_type, alert_state_key, message_prefix, alert_level="Orange"):
    """Helper to generate GDACS alert messages for specific types."""
    alerts = _get_gdacs_alerts_data(event_type_filter=event_type, alert_level_filter=alert_level)
    if alerts:
        latest_alert = alerts[0]
        message = (
            f"{message_prefix} {latest_alert['alert_level']} alert for {latest_alert['title']}. "
            f"More info: {latest_alert['link']}"
        )
        return message, latest_alert['id']
    return "", None

@app.route('/flood')
def get_flood_alert():
    current_time = time.time()
    message, event_id = _get_gdacs_alert_message("FL", "last_flood_id", "⚠️ FLOOD ALERT!")
    if message and event_id != alert_state["last_flood_id"] and \
       (current_time - alert_state["last_flood_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_flood_id"] = event_id
        alert_state["last_flood_post_time"] = current_time
        return message
    return ""

@app.route('/tropical_cyclone')
def get_tropical_cyclone_alert():
    current_time = time.time()
    message, event_id = _get_gdacs_alert_message("TC", "last_tropical_cyclone_id", "🌀 TROPICAL CYCLONE ALERT!")
    if message and event_id != alert_state["last_tropical_cyclone_id"] and \
       (current_time - alert_state["last_tropical_cyclone_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_tropical_cyclone_id"] = event_id
        alert_state["last_tropical_cyclone_post_time"] = current_time
        return message
    return ""

# --- Wildfire Alert Endpoint (NASA EONET) ---
def _get_wildfire_alert_message():
    """Internal function to get wildfire alert message without cooldown logic."""
    params = {
        "status": "open",
        "category": "wildfires",
        "limit": 1,
        "days": 7 # Look for events in the last 7 days
    }
    data = fetch_data_with_backoff(EONET_API, params=params)

    if data and data.get('events'):
        latest_wildfire = data['events'][0]
        title = str(latest_wildfire.get('title', 'Unknown Wildfire'))
        link = str(latest_wildfire.get('link', 'No link'))
        latest_wildfire_id = str(latest_wildfire.get('id'))
        message = (
            f"🔥 WILDFIRE ALERT! 🔥 Active wildfire: {title}. "
            f"More info: {link}"
        )
        return message, latest_wildfire_id
    return "", None

@app.route('/wildfire')
def get_wildfire_alert():
    current_time = time.time()
    message, event_id = _get_wildfire_alert_message()
    if message and event_id != alert_state["last_wildfire_id"] and \
       (current_time - alert_state["last_wildfire_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_wildfire_id"] = event_id
        alert_state["last_wildfire_post_time"] = current_time
        return message
    return ""

# --- Drought Alert Endpoint (GDACS International) ---
@app.route('/drought')
def get_drought_alert():
    current_time = time.time()
    message, event_id = _get_gdacs_alert_message("DR", "last_drought_id", "🏜️ DROUGHT ALERT!")
    if message and event_id != alert_state["last_drought_id"] and \
       (current_time - alert_state["last_drought_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_drought_id"] = event_id
        alert_state["last_drought_post_time"] = current_time
        return message
    return ""

# --- General Severe Weather Alert Endpoint (Leveraging GDACS for broad categories) ---
def _get_general_severe_weather_alert_message():
    """Internal function to get general severe weather alert message without cooldown logic."""
    all_gdacs_alerts = _get_gdacs_alerts_data(alert_level_filter="Orange") + \
                       _get_gdacs_alerts_data(alert_level_filter="Red")
    
    # Filter out alerts already handled by dedicated endpoints (EQ, FL, TC, VO, WF, DR)
    # Tsunami (TS) is handled by NWS, so we don't need to filter that from GDACS here.
    filtered_alerts = [
        alert for alert in all_gdacs_alerts
        if alert['event_type'] not in ["EQ", "FL", "TC", "VO", "WF", "DR"]
    ]

    if filtered_alerts:
        latest_alert = filtered_alerts[0]
        latest_general_alert_id = f"{latest_alert['event_type']}-{latest_alert['alert_level']}-{latest_alert['id']}"
        message = (
            f"⚠️ SEVERE WEATHER ALERT! ⚠️ {latest_alert['alert_level']} alert for {latest_alert['event_type']}: "
            f"{latest_alert['title']}. More info: {latest_alert['link']}"
        )
        return message, latest_general_alert_id
    return "", None

@app.route('/severe_weather_general')
def get_general_severe_weather_alert():
    current_time = time.time()
    message, event_id = _get_general_severe_weather_alert_message()
    if message and event_id != alert_state["last_general_severe_weather_id"] and \
       (current_time - alert_state["last_general_severe_weather_post_time"]) > COOLDOWN_SECONDS:
        alert_state["last_general_severe_weather_id"] = event_id
        alert_state["last_general_severe_weather_post_time"] = current_time
        return message
    return ""

# --- Chat Command Endpoint ---
@app.route('/command')
def handle_command():
    """
    Handles chat commands for on-demand natural disaster alerts.
    Commands bypass the automated cooldowns.
    Usage: YOUR_PROXY_SERVICE_URL/command?cmd=earthquake
    """
    command = request.args.get('cmd', '').lower()
    
    # Call the appropriate alert function based on the command
    if command == 'earthquake':
        message, _ = _get_earthquake_alert_message()
        return message if message else "No recent significant earthquake found."
    
    elif command == 'tsunami':
        message, _ = _get_tsunami_alert_message()
        return message if message else "No active tsunami warnings/advisories found."
        
    elif command == 'volcano':
        message, _ = _get_volcano_alert_message()
        return message if message else "No recent significant volcanic activity found."

    elif command == 'flood':
        message, _ = _get_gdacs_alert_message("FL", None, "⚠️ FLOOD ALERT!", alert_level="Orange")
        return message if message else "No recent significant global flood alerts found."

    elif command == 'cyclone' or command == 'tropicalcyclone':
        message, _ = _get_gdacs_alert_message("TC", None, "🌀 TROPICAL CYCLONE ALERT!", alert_level="Orange")
        return message if message else "No active global tropical cyclone alerts found."

    elif command == 'wildfire':
        message, _ = _get_wildfire_alert_message()
        return message if message else "No active global wildfire alerts found."

    elif command == 'drought':
        message, _ = _get_gdacs_alert_message("DR", None, "🏜️ DROUGHT ALERT!", alert_level="Orange")
        return message if message else "No recent significant global drought alerts found."

    elif command == 'weather' or command == 'severeweather':
        message, _ = _get_general_severe_weather_alert_message()
        return message if message else "No recent general severe weather alerts found."
    
    else:
        return "Unknown command. Try !earthquake, !tsunami, !volcano, !flood, !cyclone, !wildfire, !drought, or !weather."

# --- Root Endpoint (for testing the proxy itself) ---
@app.route('/')
def index():
    return "Natural Disaster Chatbot Proxy is running. Endpoints: /earthquake, /tsunami, /volcano, /flood, /tropical_cyclone, /wildfire, /drought, /severe_weather_general, /command?cmd=[type]."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)

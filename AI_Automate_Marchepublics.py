from playwright.sync_api import sync_playwright, TimeoutError
import re
import os
from datetime import datetime
from urllib.parse import urljoin
import pyautogui
import pygetwindow as gw
import time
from pywinauto import Application, timings, findwindows
from pdf_price_extractor import get_price_from_zip  # PDF EXTRACTION MODULE
from google import genai
from google.genai import types

# --- CONFIGURATION ---
USER_DATA_DIR = r"C:\Users\hp\AppData\Local\BraveSoftware\Brave-Browser\User Data"
PROFILE_NAME = "Default"
URL = "https://www.marchespublics.gov.ma/pmmp/"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
PROCESSED_FILE = "processed_tenders.txt"

# --- GEMINI API CONFIG ---
GEMINI_API_KEY = "AQ.Ab8RN6L_THx_EQge9kV5lxFzRGFtCKy6985musRmfkB5QsDBUQ"
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY # Set for potential sub-processes
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# --- TOKEN CONFIG ---
CERT_TITLE = "Choisissez un certificat"
PIN_TITLE = "Connexion au token"
IMG_NAME = "Name.png"
IMG_VALIDER = "Valider.png"
MY_PIN = "215093"

# --- CALIBRATED SPEED SETTINGS ---
pyautogui.PAUSE = 0.05 
timings.Timings.fast() 

# --- LOGIN CREDENTIALS ---
LOGIN_USER = '003224116000079'
LOGIN_PASS = 'iakb70tTA@'

# --- FORM DATA ---
MY_DATA = {
    'address': 'MAGASIN DR SOUAKA COMMUNE SETTAT -MACHRAA BEN ABBOU',
    'phone': ' 0602777619',
    'email': 'hassanelasli16@gmail.com',
    'rib': '007610000088500000114752',
    'tax_id': ' 40002272',
    'cnss_id': '2894672'
}

# --- TARGET PRICES (FALLBACK ONLY IF AI FAILS) ---
TARGET_PRICES = {
    "heure": 17.92,
    "jour": 143.36,
    "mois": 4144.00
}

# --- SETTINGS ---
USE_FILTER = True

# --- COMPETITIVE PRICING ---
PRICE_UNDERCUT = 0  # DH to subtract from every price (0 = disabled)

# ---------------- AI UTILS ----------------
import json

def extract_ai_data(description, unite, quantity):
    """
    Uses gemini to EXTRACT structured data from the description.
    AI receives: description + unite cell + quantity for full context.
    AI only extracts - Python does all calculations.
    Returns a dictionary with extracted fields.
    """
    if not description or not description.strip():
        return None

    # 🛡️ SANITIZATION SHIELD: Fix merged tokens (e.g. "12Heures" -> "12 Heures")
    # This prevents tokenization errors that cause AI to miss data
    description = re.sub(r'(?<=\d)(?=[a-zA-Z])', ' ', description)
    print(f"🛡️ Sanitized Description: {description[:60]}...")


    prompt = f"""[ROLE]
You are a JSON Extraction Expert for Moroccan Public Tenders.
Your goal is to understand the description's meaning perfectly—regardless of phrasing—and extract structured data.
DO NOT CALCULATE. DO NOT INVENT. Return ONLY valid JSON.

[OUTPUT_SCHEMA]
{{
    "unit_type": "string", 
    "num_agents": "integer", 
    "salary_per_agent": "number|null", 
    "price_ttc": "number|null", 
    "price_ht": "number|null", 
    "num_months": "integer", 
    "explicit_min_price": "number|null", 
    "min_price_is_ttc": "boolean", 
    "min_price_is_hourly": "boolean", 
    "min_price_is_daily": "boolean", 
    "hours_per_day": "integer", 
    "is_7_days_work": "boolean",
    "days_per_week": "integer",
    "price_is_for_how_many_months": "integer",
    "ai_estimated_price": "number|null"
}}

[COGNITIVE_PROCESS]
1. IDENTIFY UNIT (Strictly via Row Context).
2. NORMALIZE UNITS: Convert unknown time units to standard ones for Python calculation:
   - "Trimestre" (3 months) -> return "mois" + num_months=3
   - "Semestre" (6 months) -> return "mois" + num_months=6
   - "Année" (12 months) -> return "mois" + num_months=12
   - "Semaine" (7 days) -> return "jour"
3. CHECK CONSTAINTS (Warnings, Duration).
4. EXTRACT VALUES.
5. INTELLIGENT ESTIMATION (Products & Consumables):
   - If the unit is a PRODUCT (Litre, Unité, U, Pièce, Kit, Bidon)...
   - AND the text does not contain a specific price...
   - YOU MUST ESTIMATE the reasonable wholesale price in MOROCCO (in DH HT - Hors Taxe).
   - If you estimate a range (e.g. 15-20 DH), return the AVERAGE (e.g. 17.5).
   - Example: "Eau de Javel (5 Litres)" -> Estimate ~15-20 DH HT -> Return 17.5.
   - Put this estimated value in "ai_estimated_price".

═══════════════════════════════════════════════════════════════════════
CRITICAL: TABLE QUANTITY COLUMN LOGIC
═══════════════════════════════════════════════════════════════════════
The website table has a QUANTITY column that ALREADY contains:
- If unit = "personne" → Quantity = count of PERSONS (table handles persons)
- If unit = "mois" → Quantity = count of MONTHS (table handles months)
- If unit = "heure" → Quantity = count of HOURS (table handles hours)
- If unit = "jour" → Quantity = count of DAYS (table handles days)

⚠️ PYTHON CALCULATION USES:
- personne: salary × num_months → NEEDS num_months
- mois: num_agents × salary → NEEDS num_agents
- heure: hourly rate only → default 17.92
- jour: daily rate only → default 143.36 (or hourly min × hours)

═══════════════════════════════════════════════════════════════════════
EXTRACTION RULES (12 fields)
═══════════════════════════════════════════════════════════════════════

1. unit_type: Detect from UNITÉ DE MESURE field (never from description!)
   ⚠️ CRITICAL: Description may mention "heures" for schedules, IGNORE IT for unit detection!
   
   | Return Value | Keywords in Unité field |
   |--------------|-------------------------|
   | "heure"      | heure, heures, h, horaire, /h, par heure, hr, hrs |
   | "jour"       | jour, jours, j, journée, /j, par jour, quotidien |
   | "mois"       | mois, m, mensuel, /mois, par mois, mensuellement |
   | "personne"   | personne, agent, homme, femme, vigile, gardien, jardinier, ouvrier, employé |
   | "forfait"    | forfait, forfaitaire, global, lot, ensemble |
   | "product"    | Si Unité indique OBJET PHYSIQUE (ex: borne, extincteur, projecteur, imprimante, etc.) |
   | "unknown"    | (if none match)

FORMULA UNITS (When Unité contains expressions):
   | Unité Contains | Interpretation | Return unit_type |
   |----------------|----------------|------------------|
   | "Nombre d'agents * X mois" | Combined agent-months | "mois" |
   | "Agent * Mois", "Agent/Mois" | Same | "mois" |
   | "Heures * Jours", "H*J" | Total hours | "heure" |
   
   ⚠️ For formula units: Python returns BASE rate. Website × Quantity = Total. |

2. num_agents: Count workers in DESCRIPTION
   - Patterns: "3 agents", "cinq vigiles", "2 femmes de ménage"
   - French numbers: un=1, deux=2, trois=3, quatre=4, cinq=5, six=6, sept=7, huit=8, neuf=9, dix=10
   - Default: 1 (if not mentioned)
   - Note: When unit_type="personne", this is informational only (table has quantity)

3. salary_per_agent: Monthly salary if mentioned
   - Look for: "X DH par agent", "salaire de X", "rémunération X"
   - ⚠️ IGNORE 17.92 (that's hourly SMIG, not monthly salary!)
   - Default: null

4. price_ttc: Total TTC price if mentioned
   - Look for: "X DH TTC", "toutes taxes comprises"
   - Default: null

5. price_ht: Total HT price if mentioned
   - Look for: "X DH HT", "hors taxes"
   - Default: null

6. num_months: Duration in months
   - Date ranges: "du 01/02/2026 au 31/03/2026" = count months
   - Explicit: "durant 6 mois", "période de 12 mois"
   - Default: 1

7. explicit_min_price: Minimum price if stated
   - Keywords: "offre inférieure à X sera écartée", "minimum", "plancher", "supérieur à", "est égale de", "est fixée à", "est de",
   - ARABIC KEYWORDS: "يقل عن", "الحد الأدنى", "أقل من", "مبلغ أدنى", "تقدير الكلفة", "محدد في مبلغ"
   - ⚠️ CRITICAL EXCLUSION: IGNORE strict legal references like "selon le décret... 17.92 dhs/heure", "salaire minimum légal (17.92)", or "salaire journalier de 143.36 DH".
   - REASON: These are legal citations (Base Salary ONLY), NOT the bid price limit (which usually includes Charges + Margin).
   - ONLY extract 17.92/143.36 if the text EXPLICITLY says "offre COMPRENANT CHARGES inférieure à 143.36 sera rejetée" (rare).
   - Default: null (This triggers PDF Extraction for the real breakdown!)

8. min_price_is_ttc: Is explicit_min_price stated as TTC?
   - true if followed by "TTC" or "toutes taxes comprises"
   - Default: false

9. min_price_is_hourly: Is explicit_min_price stated per HOUR?
   - true if contains "heure", "/h", "par heure", "dh/heure"
   - Example: "24 dh/agent/heure" → true
   - Default: false

10. min_price_is_daily: Is explicit_min_price stated per DAY?
    - true if contains "jour", "/j", "par jour", "dh/jour", "journalier"
    - Default: false

11. hours_per_day: Hours per day if mentioned
    - Look for: "8H par jour", "4 heures/jour", "6h/j"
    - Default: 8

12. is_7_days_work: Does the work include ALL days (including Sunday/Holidays)?
    - true if "7j/7", "7/7", "tous les jours", "dimanche inclus", "jours fériés inclus"
    - Default: false

13. days_per_week: Number of work days per week
    - "5j/7", "du lundi au vendredi" -> 5
    - "4 jours par semaine" -> 4
    - "tout les jours", "7j/7" -> 7
    - Default: 6 (Standard)

14. price_is_for_how_many_months: How many months are covered by the 'explicit_min_price'?
   - If text says "19,990.43 for 2 months" or "pour la période de 2 mois" -> return 2.
   - If text says "par agent et par mois" or "mensuel" -> return 1.
   - Default: 1.

═══════════════════════════════════════════════════════════════════════
EXAMPLES (showing what Python does with each unit type)
═══════════════════════════════════════════════════════════════════════

Example 1 (unit=jour, quantity=60 days):
INPUT: "Toute offre inférieure à 24 dh/heure sera écartée. 8H par jour, 60 jours" | ROW: "Jour"
OUTPUT: {{"unit_type": "jour", "num_agents": 1, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 1, "explicit_min_price": 24, "min_price_is_ttc": false, "min_price_is_hourly": true, "hours_per_day": 8}}
→ Python: 24 × 8 = 192 (hourly→daily) | Website: 192 × 60 = 11,520

Example 2 (unit=mois, quantity=6 months):
INPUT: "3 agents de sécurité, salaire 4000 DH, du 01/01/2026 au 30/06/2026" | ROW: "Mois"
OUTPUT: {{"unit_type": "mois", "num_agents": 3, "salary_per_agent": 4000, "price_ttc": null, "price_ht": null, "num_months": 6, "explicit_min_price": null, "min_price_is_ttc": false, "min_price_is_hourly": false, "hours_per_day": 8}}
→ Python: 3 × 4000 = 12,000 (agents × salary) | Website: 12,000 × 6 = 72,000

Example 3 (unit=personne, quantity=2 persons):
INPUT: "2 personnes, contrat 12 mois, respect du SMIG obligatoire" | ROW: "Personne"
OUTPUT: {{"unit_type": "personne", "num_agents": 2, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 12, "explicit_min_price": null, "min_price_is_ttc": false, "min_price_is_hourly": false, "hours_per_day": 8}}
→ Python: 4144 × 12 = 49,728 (salary × months) | Website: 49,728 × 2 = 99,456

Example 4 (unit=heure, quantity=480 hours):
INPUT: "respect du SMIG, 8h/jour pendant 60 jours" | ROW: "Heure"
OUTPUT: {{"unit_type": "heure", "num_agents": 1, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 1, "explicit_min_price": null, "min_price_is_ttc": false, "min_price_is_hourly": false, "hours_per_day": 8}}
→ Python: 17.92 (default hourly) | Website: 17.92 × 480 = 8,601.60

Example 5 (unit=heure, quantity=13224 hours)(EXPLICIT PRICE "est égale de"):
INPUT: "NB : le prix d’une heure de travail respectant le SMIG est égale de 22,94 dh" | ROW: "Heure"
OUTPUT: {{"unit_type": "heure", "num_agents": 1, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 1, "explicit_min_price": 22.94, "min_price_is_ttc": false, "min_price_is_hourly": true, "hours_per_day": 8}}
→ Python: 22.94 (Exact extraction) | Website: 22.94 × 13224 = 299,977.60

Example 6 (unit=jour, quantity=480 days)(EXPLICIT HOURLY PRICE needing conversion to DAY):
INPUT: "NB : le prix d’une heure de travail respectant le SMIG est égale de 22,94 dh" | ROW: "Jour"
OUTPUT: {{"unit_type": "jour", "num_agents": 1, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 1, "explicit_min_price": 22.94, "min_price_is_ttc": false, "min_price_is_hourly": true, "hours_per_day": 8}}
→ Python: 22.94 × 8 = 183.52 (Hourly 22.94 converted to Daily by Python) | Website: 183.52 × 480 = 88,089.60

Example 7 (unit=mois, quantity=8 months) (unit=mois, explicitly DAILY price, 7/7 work):
INPUT: "4 AGENTS... 8 HEURES PAR JOUR 7/7J... prix unitaire journalier doit être supérieur à 190,56 dh" | ROW: "Mois"
OUTPUT: {{"unit_type": "mois", "num_agents": 4, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 8, "explicit_min_price": 190.56, "min_price_is_ttc": false, "min_price_is_hourly": false, "min_price_is_daily": true, "hours_per_day": 8, "is_7_days_work": true}}
→ Python: 4 agents × (190.56 × 30 days) = 22,867.20 | Website: 22,867.20 × 8 = 182,937.60

Example 8 (unit=mois, quantity=8 months) (unit=mois, explicitly HOURLY price, 7/7 work):
INPUT: "4 AGENTS... 8 HEURES PAR JOUR 7/7J... prix heure supérieur à 23,00 dh" | ROW: "Mois"
OUTPUT: {{"unit_type": "mois", "num_agents": 4, "salary_per_agent": null, "price_ttc": null, "price_ht": null, "num_months": 8, "explicit_min_price": 23.00, "min_price_is_ttc": false, "min_price_is_hourly": true, "min_price_is_daily": false, "hours_per_day": 8, "is_7_days_work": true}}
→ Python: 4 agents × (23.00 × 8h × 30 days) = 22,080.00 | Website: 22,080.00 × 8 = 176,640.00

[INPUT_DATA]
Désignation: "{description}"
Unité de mesure: "{unite}"
Quantité: "{quantity}"
"""

    try:
        print("🤖 Asking Gemini 3 flash (HIGH)...")
        
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            ),
        ]

        full_response_text = ""
         
        # --- ATTEMPT 1: PRIMARY (Thinking Mode) ---
        try:
            primary_config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
                media_resolution="MEDIA_RESOLUTION_UNSPECIFIED",
                response_mime_type="application/json",
            )
            
            for chunk in gemini_client.models.generate_content_stream(
                model="gemini-3-flash-preview",
                contents=contents,
                config=primary_config,
            ):
                full_response_text += (chunk.text or "")

        except Exception as e:
            # --- IMMEDIATE FAILOVER VALIDATION ---
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "500" in error_msg or "504" in error_msg or "Overloaded" in error_msg:
                print(f"⚠️ Recoverable Error ({error_msg}). FAILOVER to Gemini 2.5 Pro...")
                
                # --- ATTEMPT 2: FALLBACK (Gemini 2.5 Pro) ---
                fallback_config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=8192),
                    response_mime_type="application/json",
                )
                
                full_response_text = "" # Reset buffer
                for chunk in gemini_client.models.generate_content_stream(
                    model="gemini-2.5-pro",
                    contents=contents,
                    config=fallback_config,
                ):
                    full_response_text += (chunk.text or "")
            else:
                # If it's not a 503 (e.g. Auth Error), re-raise it
                raise e

        # Extract JSON from potential thought blocks
        match = re.search(r'\{.*\}', full_response_text, re.DOTALL)
        if match:
            clean_json = match.group(0)
            data = json.loads(clean_json)

            # --- ROUNDING SHIELD (Precision Fix) ---
            for key in ["salary_per_agent", "price_ttc", "price_ht", "explicit_min_price"]:
                if data.get(key) is not None and isinstance(data[key], (int, float)):
                     data[key] = round(float(data[key]), 2)

            print(f"✅ Gemini Extracted: {data}")
            return data
        else:
            print(f"⚠️ Gemini output no JSON: {full_response_text[:100]}...")
            return None

    except Exception as e:
        print(f"⚠️ Gemini API Error: {e}")
        return None

def detect_unit_from_row(row):
    """
    Python scans all cells in the row looking for a short cell 
    that contains a unit keyword. Returns the unit type.
    """
    unit_keywords = {
        # HEURE
        "heure": "heure", "heures": "heure", "h": "heure", "horaire": "heure", "/h": "heure", "h/": "heure",
        "hr": "heure", "hrs": "heure",
        
        # JOUR
        "jour": "jour", "jours": "jour", "j": "jour", "journée": "jour", "journées": "jour",
        "journalier": "jour", "/j": "jour", "j/": "jour", "quotidien": "jour", "quotidienne": "jour",
        
        # MOIS
        "mois": "mois", "m": "mois", "mensuel": "mois", "mensuelle": "mois", "mensualité": "mois",
        "/mois": "mois", "m/": "mois", "mensuellement": "mois",
        
        # PERSONNE/AGENT
        "personne": "personne", "personnes": "personne", "pers": "personne",
        "agent": "personne", "agents": "personne",
        "homme": "personne", "hommes": "personne",
        "femme": "personne", "femmes": "personne",
        "vigile": "personne", "vigiles": "personne",
        "gardien": "personne", "gardiens": "personne",
        "ouvrier": "personne", "ouvriers": "personne", "ouvrière": "personne",
        "technicien": "personne", "techniciens": "personne",
        "opérateur": "personne", "opérateurs": "personne",
        "intervenant": "personne", "intervenants": "personne",
        "jardinier": "personne", "jardiniers": "personne",
        "nettoyeur": "personne", "nettoyeurs": "personne",
        "employé": "personne", "employés": "personne",
        "travailleur": "personne", "travailleurs": "personne",
        "personnel": "personne", "effectif": "personne", "main d'oeuvre": "personne",
        
        # FORFAIT
        "forfait": "forfait", "forfaitaire": "forfait", "global": "forfait", "globale": "forfait",
        "ensemble": "forfait", "lot": "forfait"
    }
    
    try:
        # Get all td cells in the row
        cells = row.locator("td").all()
        
        for cell in cells:
            text = cell.inner_text().strip().lower()

            # Only check SHORT cells (unit cells are short, description is long)
            if len(text) < 20: 
                # Special check for "Mois / Agent" combined unit
                if "mois" in text and ("agent" in text or "personne" in text):
                     return "mois_agent"
                
                # Check standard keywords
                if text in unit_keywords:
                    return unit_keywords[text]
    except:
        pass
    
    return "unknown"

def extract_row_context(row):
    """
    Extract unit cell, quantity, and description SEPARATELY from table row.
    Uses td.quantity as anchor for stable extraction.
    Unité is always the cell BEFORE td.quantity.
    """
    cells = row.locator("td").all()
    
    context = {
        "description": "",
        "unite": "",
        "quantity": ""
    }
    
    for i, cell in enumerate(cells):
        class_attr = cell.get_attribute("class") or ""
        
        # Description has "text-start" class
        if "text-start" in class_attr:
            context["description"] = cell.inner_text().strip()
        
        # Quantity has "quantity" class  
        if "quantity" in class_attr:
            context["quantity"] = cell.inner_text().strip()
            # Unité is the cell BEFORE quantity
            if i > 0:
                context["unite"] = cells[i - 1].inner_text().strip()
    
    return context

def safe_parse_quantity(q_str):
    """
    Robustly parses quantity strings like '16 192', '10,5', '100'.
    Returns float or 0 if invalid.
    """
    if not q_str: return 0
    try:
        # Standardize format: "16 192,5" -> "16192.5"
        clean = str(q_str).strip().replace(" ", "").replace(",", ".")
        return float(clean)
    except:
        return 0

def calculate_final_price(data, row_context, python_detected_unit=None, quantity_str=None):
    """
    Python function that calculates the final price based on extracted AI data.
    All math operations are done here - guaranteed accuracy.
    """
    if not data:
        return None
    
    # Default values
    DEFAULT_HOURLY = 17.92
    DEFAULT_DAILY = 143.36
    DEFAULT_MONTHLY = 4144.00
    
    # Unit Type Logic: Python override > AI extraction
    unit_type = data.get("unit_type", "unknown").lower()
    if python_detected_unit and python_detected_unit != "unknown":
        unit_type = python_detected_unit
        print(f"🔹 Python Override: Forced unit type to '{unit_type}' (from table)")
    num_agents = data.get("num_agents") or 1
    salary_per_agent = data.get("salary_per_agent")
    price_ttc = data.get("price_ttc")
    price_ht = data.get("price_ht")
    num_months = data.get("num_months") or 1
    explicit_min_price = data.get("explicit_min_price")
    min_price_is_ttc = data.get("min_price_is_ttc", False)
    min_price_is_hourly = data.get("min_price_is_hourly", False)
    hours_per_day = data.get("hours_per_day", 8) or 8  # Default 8 hours if not specified (guards against 0)
    
    # --- OPTIMIZED MULTIPLIER LOGIC (Centralized) ---
    # 1. Get Days (Default 6)
    days = data.get("days_per_week", 6)
    if data.get("is_7_days_work"): days = 7
    
    # 2. Calculate Multiplier (Aggressive Market Logic)
    if days == 7: 
        work_days_multiplier = 30   # Full Month (Safe)
    elif days == 6: 
        work_days_multiplier = 26   # Standard Work Month (Safe)
    else: 
        work_days_multiplier = days * 4  # Aggressive: 5 days -> 20 (Market Math)
    # ------------------------------------------------
    
    # Priority 1: Explicit minimum price
    if explicit_min_price:
        final = float(explicit_min_price)
        original_price = final  # Save original for threshold checks (before TTC conversion)
        
        # Priority 1A: Handle TTC conversion first (Global Rule)
        if min_price_is_ttc:
            final = round(final / 1.2, 2)
            print(f"🧮 TTC Conversion: {explicit_min_price} / 1.2 = {final} HT")

        # Parse Quantity once (Universal "Source of Truth")
        qty = safe_parse_quantity(quantity_str)

        # CASE 1: Personne/Agent (Divide by Table Quantity)
        # We trust the Table Quantity ("23") more than AI's "num_agents"
        if unit_type in ["personne", "agent"] and qty > 1:
            if original_price > 6000:
                # 1. Step: Get price for one agent total duration (e.g. 19990.43 / 2 agents = 9995.21)
                salary_step = final / qty
                
                # 2. Step: Get threshold duration from AI (e.g. 2 months for Jardinage)
                months_in_threshold = data.get("price_is_for_how_many_months", 1) or 1
                
                # 3. Step: Divide total per agent by months to get MONTHLY salary (e.g. 9995.21 / 2 = 4997.61)
                salary_per_agent = salary_step / months_in_threshold
                
                # 4. Step: Multi-Month Logic
                if months_in_threshold > 1:
                    # Multi-month sum: Return ONLY the monthly salary (as user requested)
                    final = round(salary_per_agent, 2)
                    print(f"🧮 Master Logic: Global Sum for {months_in_threshold}m. Monthly rate: {final}")
                else:
                    # Standard Sum (1 month): Return Salary * num_months
                    final = round(salary_per_agent * num_months, 2)
                    print(f"🧮 Master Logic: Monthly threshold. Total for {num_months}m: {final}")
                
                return str(final)

        # CASE 1A: Personne/Agent with qty=1 but high global sum
        if unit_type in ["personne", "agent"] and qty <= 1:
            if original_price > 6000:
                months_duration = data.get("price_is_for_how_many_months", 1) or 1
                if months_duration > 1:
                    final = round(final / months_duration, 2)
                    print(f"🧮 Master Logic (Personne qty=1): Global Sum {original_price} / {months_duration} months = {final}")
                    return str(final)

        # CASE 1B: Mois (High Price Division) - The "Zero-Failure" Safety Net
        # Handles El Jadida and multi-month global sums
        if unit_type == "mois" and qty > 1:
            if original_price > 6000:
                # 🛡️ THE GRAND-MASTER SAFETY NET:
                # Check if AI found an EXPLICIT duration (e.g. "for 2 months")
                explicit_duration = data.get("price_is_for_how_many_months", 1) or 1
                
                # If NO explicit duration in text, ANCHOR to the Table Quantity (Safety Divider)
                effective_divider = explicit_duration if explicit_duration > 1 else qty
                
                monthly_rate = final / effective_divider
                final = round(monthly_rate, 2)
                
                print(f"🧮 Master Safety Logic (Mois): Global Threshold ({original_price}).")
                print(f"   -> AI Duration: {explicit_duration}, Table Qty: {qty}. Using Divider: {effective_divider}")
                print(f"   -> Calculated Monthly Rate: {final}")
                
                return str(final)

        # CASE 2: Heure/Jour (Divide by Table Quantity)
        elif (unit_type == "heure" and original_price > 50) or (unit_type == "jour" and original_price > 300):
            if qty > 0:
                 print(f"🧮 Master Logic ({unit_type}): Detected High Price {explicit_min_price}. Dividing by Quantity: {qty}")
                 
                 # TTC already handled by Global Rule at Priority 1A (line 469)
                 
                 # Divide by Quantity
                 final = round(final / qty, 2)
                 print(f"   -> Final Unit Price: {final}")
                 return str(final)
        
        # Handle hourly-to-daily conversion (if min is hourly but unit is jour)
        if min_price_is_hourly and unit_type == "jour":
            final = round(final * hours_per_day, 2)  # Use extracted hours per day
            print(f"🧮 Python Calculated: {final} (hourly min {explicit_min_price} × {hours_per_day} hours)")
            return str(final)

        # Handle hourly-to-monthly conversion (if min is hourly but unit is mois)
        elif min_price_is_hourly and unit_type == "mois":
            # Uses Centralized 'work_days_multiplier' (Safe & Consolidated)
            final = round(num_agents * final * hours_per_day * work_days_multiplier, 2)
            print(f"🧮 Python Calculated: {final} (hourly→monthly: {explicit_min_price}/hr × {hours_per_day}h × {work_days_multiplier}d × {num_agents} agents)")
            return str(final)

        # Handle daily-to-monthly conversion (if min is daily but unit is mois)
        elif data.get("min_price_is_daily", False) and unit_type == "mois":
            # Uses Centralized 'work_days_multiplier' (Safe & Consolidated)
            final = round(num_agents * final * work_days_multiplier, 2)
            print(f"🧮 Python Calculated: {final} (daily→monthly: {explicit_min_price}/day × {work_days_multiplier}d × {num_agents} agents)")
            return str(final)

        # Handle hourly-to-personne conversion (if min is hourly but unit is personne/agent)
        elif min_price_is_hourly and unit_type in ["personne", "agent"]:
            # Uses Centralized 'work_days_multiplier'
            final = round(final * hours_per_day * work_days_multiplier * num_months, 2)
            print(f"🧮 Python Calculated: {final} (hourly→personne: {explicit_min_price}/hr × {hours_per_day}h × {work_days_multiplier}d × {num_months} months)")
            return str(final)

        # Handle daily-to-personne conversion (if min is daily but unit is personne/agent)
        elif data.get("min_price_is_daily", False) and unit_type in ["personne", "agent"]:
            # Uses Centralized 'work_days_multiplier'
            final = round(final * work_days_multiplier * num_months, 2)
            print(f"🧮 Python Calculated: {final} (daily→personne: {explicit_min_price}/day × {work_days_multiplier}d × {num_months} months)")
            return str(final)

        # Handle hourly-to-mois_agent conversion (if min is hourly but unit is mois_agent)
        elif min_price_is_hourly and unit_type == "mois_agent":
            # Unitary monthly conversion - NO num_agents multiplier
            final = round(final * hours_per_day * work_days_multiplier, 2)
            print(f"🧮 Python Calculated: {final} (hourly→mois_agent: {explicit_min_price}/hr × {hours_per_day}h × {work_days_multiplier}d)")
            return str(final)

        # Handle daily-to-mois_agent conversion (if min is daily but unit is mois_agent)
        elif data.get("min_price_is_daily", False) and unit_type == "mois_agent":
            # Unitary monthly conversion - NO num_agents multiplier
            final = round(final * work_days_multiplier, 2)
            print(f"🧮 Python Calculated: {final} (daily→mois_agent: {explicit_min_price}/day × {work_days_multiplier}d)")
            return str(final)

        elif min_price_is_ttc:
            print(f"🧮 Python Calculated: {final} (explicit minimum TTC {explicit_min_price} / 1.2)")
            return str(final)

        else:
            print(f"🧮 Python Calculated: {explicit_min_price} (explicit minimum HT)")
            return str(explicit_min_price)
    
    # Priority 2: TTC price (divide by 1.2)
    if price_ttc:
        final = round(price_ttc / 1.2, 2)
        print(f"🧮 Python Calculated: {final} (TTC {price_ttc} / 1.2)")
        return str(final)
    
    # Priority 3: HT price (use as-is)
    if price_ht:
        print(f"🧮 Python Calculated: {price_ht} (HT price)")
        return str(price_ht)
    
    # Priority 4: Calculate based on unit type
    row_lower = row_context.lower() if row_context else ""
    
    # Detect unit from row_context if AI couldn't determine (COMPREHENSIVE FALLBACK)
    if unit_type == "unknown":
        # HEURE detection
        if any(x in row_lower for x in ["heure", "heures", " h ", "h/", "/h", "horaire", "par heure", "à l'heure", "hr", "hrs"]):
            unit_type = "heure"
        # JOUR detection
        elif any(x in row_lower for x in ["jour", "jours", " j ", "j/", "/j", "journée", "journées", "journalier", "par jour", "quotidien"]):
            unit_type = "jour"
        # MOIS detection
        elif any(x in row_lower for x in ["mois", " m ", "m/", "/mois", "mensuel", "mensuelle", "mensualité", "par mois", "mensuellement"]):
            unit_type = "mois"
        # PERSONNE/AGENT detection (comprehensive)
        elif any(x in row_lower for x in [
            "personne", "personnes", "pers", "agent", "agents",
            "homme", "hommes", "femme", "femmes",
            "vigile", "vigiles", "gardien", "gardiens",
            "ouvrier", "ouvriers", "ouvrière", "ouvrières",
            "technicien", "techniciens", "technicienne", "techniciennes",
            "opérateur", "opérateurs", "opératrice", "opératrices",
            "intervenant", "intervenants", "intervenante", "intervenantes",
            "jardinier", "jardiniers", "jardinière", "jardinières",
            "nettoyeur", "nettoyeurs", "nettoyeuse", "nettoyeuses",
            "employé", "employés", "employée", "employées",
            "travailleur", "travailleurs", "travailleuse", "travailleuses",
            "personnel", "effectif", "main d'oeuvre"
        ]):
            unit_type = "personne"
        # FORFAIT detection
        elif any(x in row_lower for x in ["forfait", "forfaitaire", "global", "globale", "ensemble", "lot", "prestation globale"]):
            unit_type = "forfait"
    
    # Calculate based on unit type
    if unit_type == "mois_agent":
        # Mois/Agent: Table quantity is global months (e.g. 55).
        # We just return the UNITARY monthly price per agent. NO multiplication by num_agents.
        salary = salary_per_agent
        
        # --- UNIVERSAL HOURS LOGIC (Ported from 'mois') ---
        # If no explicit salary AND hours are NON-STANDARD (not 8), calculate correct salary.
        # Handles Part-Time (<8h) AND Overtime (>8h like 12h/24h).
        if not salary and hours_per_day and hours_per_day != 8:
             final_salary = round(17.92 * hours_per_day * work_days_multiplier, 2)
             print(f"🧮 Universal Logic: Adjusted Salary for {hours_per_day}H work. Calculated {final_salary} (17.92 * {hours_per_day}h * {work_days_multiplier}d)")
             salary = final_salary

        salary = salary if salary else DEFAULT_MONTHLY
        print(f"🧮 Python Calculated: {salary} (Unit: Mois/Agent - using unitary monthly price)")
        return str(salary)

    elif unit_type == "heure":
        # No explicit price found - return None to trigger PDF extraction
        # PDF may contain the actual price table with calculations
        print(f"🔍 Unit is 'heure' but no explicit price found - will try PDF extraction")
        return None  # Trigger PDF extraction
    
    elif unit_type == "jour":
        # No explicit price found - return None to trigger PDF extraction
        # PDF may contain the actual price table with calculations
        print(f"🔍 Unit is 'jour' but no explicit price found - will try PDF extraction")
        return None  # Trigger PDF extraction
    
    elif unit_type == "mois":
        # Salary per Agent (Standard) — explicit prices handled in Priority 1
        salary = salary_per_agent
        
        # --- UNIVERSAL HOURS LOGIC (Grand Master v5.0) ---
        # If no explicit salary AND hours are NON-STANDARD (not 8), calculate correct salary.
        # Handles Part-Time (<8h) AND Overtime (>8h like 12h/24h).
        if not salary and hours_per_day and hours_per_day != 8:
             # Uses Centralized 'work_days_multiplier' (Safe & Consolidated)
             final_salary = round(17.92 * hours_per_day * work_days_multiplier, 2)
             print(f"🧮 Universal Logic: Adjusted Salary for {hours_per_day}H work. Calculated {final_salary} (17.92 * {hours_per_day}h * {work_days_multiplier}d)")
             salary = final_salary
        # ------------------------------------------------

        salary = salary if salary else DEFAULT_MONTHLY
        final = round(num_agents * salary, 2)
        print(f"🧮 Python Calculated: {final} ({num_agents} agents × {salary} salary)")
        return str(final)

    elif unit_type in ["personne", "agent"]:
        # Per person: salary × num_months
        # ⚠️ IMPORTANT: num_agents is IGNORED here because the table already has a quantity column
        # The table multiplies (unit_price × quantity) automatically
        salary = salary_per_agent
        
        # --- UNIVERSAL HOURS LOGIC (Ported from 'mois') ---
        # If no explicit salary AND hours are NON-STANDARD (not 8), calculate correct salary.
        # Handles Part-Time (<8h) AND Overtime (>8h like 12h/24h).
        if not salary and hours_per_day and hours_per_day != 8:
             final_salary = round(17.92 * hours_per_day * work_days_multiplier, 2)
             print(f"🧮 Universal Logic: Adjusted Salary for {hours_per_day}H work. Calculated {final_salary} (17.92 * {hours_per_day}h * {work_days_multiplier}d)")
             salary = final_salary

        salary = salary if salary else DEFAULT_MONTHLY
        final = round(salary * num_months, 2)
        print(f"🧮 Python Calculated: {final} ({salary} salary × {num_months} months) [num_agents ignored - table has quantity]")
        return str(final)
    
    elif unit_type == "forfait":
        # Forfait: typically total price, use agents × monthly as default
        salary = salary_per_agent if salary_per_agent else DEFAULT_MONTHLY
        final = round(num_agents * salary * num_months, 2)
        print(f"🧮 Python Calculated: {final} ({num_agents} agents × {salary} × {num_months} months)")
        return str(final)
    
    # FORMULA UNIT: "Nombre d'agents * X mois" or "Agent/Mois" or similar combinations
    elif ("agent" in unit_type.lower() and "mois" in unit_type.lower()) or \
         ("nombre" in unit_type.lower() and "mois" in unit_type.lower()):
        # It's a formula unit - return monthly salary as base rate
        # Website will multiply by quantity
        salary = salary_per_agent if salary_per_agent else DEFAULT_MONTHLY
        print(f"🧮 Formula Unit detected: '{unit_type}' - Returning base monthly {salary}")
        return str(salary)
    
    # NEW BLOCK: Check for AI Estimation
    ai_estimated_price = data.get("ai_estimated_price")
    
    # Trust AI if it estimated a price for a Product or Unknown Unit
    if ai_estimated_price:
        print(f"🧠 AI Estimated Market Price for '{unit_type}': {ai_estimated_price} DH HT")
        return str(ai_estimated_price)

    else:
        # Unknown unit type - use minimum hourly rate (SMIG) as safe fallback
        print(f"⚠️ Unknown unit type '{unit_type}', using default {DEFAULT_HOURLY} (hourly SMIG)")
        return str(DEFAULT_HOURLY)

def get_ai_price(description, row_context, python_detected_unit=None, quantity_str="1"):
    """
    Main entry point - combines AI extraction with Python calculation.
    Updated to accept quantity.
    """
    # Step 1: AI extracts structured data
    # Fix: pass quantity_str (or "1" default) to match extract_ai_data signature
    extracted_data = extract_ai_data(description, row_context, quantity_str)
    
    if not extracted_data:
        print("⚠️ AI extraction failed, will use fallback")
        return None
    
    # Step 2: Python calculates final price
    final_price = calculate_final_price(extracted_data, row_context, python_detected_unit, quantity_str)
    
    return final_price

# ---------------- UTILS ----------------
def get_french_date_label(date_str):
    if not date_str or not date_str.strip(): return None
    try:
        date_obj = datetime.strptime(date_str, "%d/%m/%Y")
        months_fr = ["janvier", "février", "mars", "avril", "mai", "juin",
                     "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        return f"{months_fr[date_obj.month - 1]} {date_obj.day},"
    except:
        return None

def smart_wait(page, selector, timeout=10000): 
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout)
        return True
    except TimeoutError:
        return False

def safe_click(locator, retries=3):
    for attempt in range(retries):
        try:
            locator.wait_for(state="visible", timeout=3000)
            locator.scroll_into_view_if_needed()
            locator.click(force=True, timeout=2000)
            return True
        except Exception:
            time.sleep(0.5) 
    return False

def safe_fill(locator, value, retries=3):
    for attempt in range(retries):
        try:
            locator.wait_for(state="visible", timeout=3000)
            current_val = locator.input_value()
            if current_val and current_val.strip() == str(value):
                return True
            locator.fill("") 
            locator.type(str(value), delay=25) 
            return True
        except Exception:
            time.sleep(0.5)
    return False

def safe_handle_dialog(dialog):
    try:
        dialog.accept()
    except Exception:
        pass

# ---------------- DESKTOP AUTOMATION (SIGNING) ----------------
def run_perfect_flow(timeout=90):
    print(f"🚀 Monitoring system for Java windows (Timeout: {timeout}s)...")

    # 1. Wait for Certificate Window
    cert_win = None
    start_wait = time.time()
    while not cert_win:
        if time.time() - start_wait > timeout: 
            print("❌ Timeout: Certificate window never appeared.")
            return False
        wins = gw.getWindowsWithTitle(CERT_TITLE)
        if wins:
            cert_win = wins[0]
            try:
                cert_win.activate()
                time.sleep(0.5) 
                break
            except:
                continue
        time.sleep(0.5)

    # region = (cert_win.left, cert_win.top, cert_win.width, cert_win.height) # NOT NEEDED FOR HIGH-DPI FIX
    
    # Select Certificate
    name_pos = None
    while not name_pos:
        # HIGH-DPI FIX: Removed region=region to allow full screen search
        name_pos = pyautogui.locateCenterOnScreen(IMG_NAME, confidence=0.8, grayscale=True)
        if name_pos:
            pyautogui.click(name_pos)
            break
        time.sleep(0.1) 

    # Click Valider
    valider_pos = None
    while not valider_pos:
        # HIGH-DPI FIX: Removed region=region to allow full screen search
        valider_pos = pyautogui.locateCenterOnScreen(IMG_VALIDER, confidence=0.8, grayscale=True)
        if valider_pos:
            pyautogui.click(valider_pos)
            break
        time.sleep(0.1)

    # 2. SMART PIN HANDLING (UIA Based)
    print("⌨️ Handing PIN Window...")
    pin_success = False
    start_pin_wait = time.time()
    
    while time.time() - start_pin_wait < 15:
        try:
            handles = findwindows.find_windows(title=PIN_TITLE)
            if handles:
                hwnd = handles[0]
                app = Application(backend="uia").connect(handle=hwnd)
                dlg = app.window(handle=hwnd)
                dlg.set_focus()

                edit_field = dlg.child_window(auto_id="1002", control_type="Edit")
                ok_btn = dlg.child_window(auto_id="1", control_type="Button")

                if edit_field.exists() and edit_field.is_visible():
                    edit_field.set_focus()
                    edit_field.type_keys(MY_PIN, with_spaces=True, pause=0.03)
                    time.sleep(0.2)
                    
                    if ok_btn.exists():
                        ok_btn.invoke() 
                        print("✅ PIN submitted via UIA.")
                        pin_success = True
                        break
        except Exception:
            time.sleep(0.3)

    if not pin_success:
        print("⚠️ UIA limited. Trying direct typing fallback...")
        pyautogui.write(MY_PIN, interval=0.03)
        pyautogui.press('enter')

    # 3. SMART BARRIER
    print("⏳ Waiting for desktop windows to finalize...")
    cleanup_start = time.time()
    while time.time() - cleanup_start < 5: 
        c_wins = gw.getWindowsWithTitle(CERT_TITLE)
        p_wins = gw.getWindowsWithTitle(PIN_TITLE)
        if not c_wins and not p_wins:
            print("✨ Windows cleared.")
            return True
        time.sleep(0.3)
    
    return True 

# ---------------- LOGIN ----------------
def login_sequence(page):
    print("🔑 Logging in...")
    try:
        guest_btn = page.locator("button:has-text('Invité')")
        guest_btn.wait_for(state="visible", timeout=7000)
        safe_click(guest_btn)
        
        login_link = page.get_by_role("link", name="Se connecter")
        login_link.wait_for(state="visible", timeout=3000)
        safe_click(login_link)
        
        page.wait_for_load_state("domcontentloaded")
        
        safe_fill(page.get_by_role("textbox", name="Login :"), LOGIN_USER)
        safe_fill(page.get_by_role("textbox", name="Mot de passe :"), LOGIN_PASS)
        
        auth_ok = page.get_by_title("Authentification - OK")
        auth_ok.wait_for(state="visible", timeout=3000)
        safe_click(auth_ok)
        
        page.wait_for_load_state("networkidle")
    except Exception as e:
        print(f"⚠️ Login error: {e}")

# ---------------- FORM FILLING & SIGNING ----------------
def fill_tender_form(page):
    print("✍️ Checking fields...")
    try:
        addr_field = page.get_by_role("textbox", name="Adresse*")
        try:
            addr_field.wait_for(state="visible", timeout=10000)
        except:
            print("⏭️ Fields not visible (already filled or expired). Skipping to next URL.")
            return

        # Identity
        fields = {"Adresse*": MY_DATA['address'], "Téléphone*": MY_DATA['phone'], "Email*": MY_DATA['email']}
        for name, value in fields.items():
            safe_fill(page.get_by_role("textbox", name=name), value)

        # RIB handling
        activate_link = page.get_by_role("link", name="Activer la saisie libre")
        deactivate_link = page.get_by_role("link", name="Désactiver la saisie libre")
        
        if deactivate_link.is_visible():
            print("ℹ️ Free RIB entry already active. Proceeding...")
        elif activate_link.is_visible():
            print("🖱️ Activating free RIB entry...")
            safe_click(activate_link)
            page.wait_for_timeout(400)

        rib_field = page.get_by_role("textbox", name="Saisissez votre RIB")
        rib_field.wait_for(state="visible", timeout=3000)
        current_rib = rib_field.input_value()
        
        if current_rib.strip() != MY_DATA['rib']:
            safe_fill(rib_field, MY_DATA['rib'])

        # Tax & CNSS
        safe_fill(page.get_by_role("textbox", name="N° d'inscription à la taxe"), MY_DATA['tax_id'])
        safe_fill(page.get_by_role("textbox", name="N° d’affiliation à la CNSS ou"), MY_DATA['cnss_id'])

        # --- PRICING TABLE WITH AI ---
        page.wait_for_selector("tr", timeout=5000)
        rows = page.locator("tr").all()
        for row in rows:
            row_text = row.inner_text().lower()
            spin = row.get_by_role("spinbutton")
            
            if spin.count() > 0:
                target_value = None
                
                # Extract all context SEPARATELY from dedicated cells
                context = extract_row_context(row)
                
                
                # Python detect unit (safe scope)
                detected_unit = detect_unit_from_row(row)

                if context["description"]:
                    # AI ANALYSIS (Universal for Services & Products)
                    print(f"📊 Row Context:")
                    print(f"   Unité: {context['unite']}")
                    print(f"   Quantité: {context['quantity']}")
                    print(f"   Description: {context['description'][:60]}...")
                    
                    if detected_unit != "unknown":
                        print(f"🔹 Python detected unit: {detected_unit}")
                    
                    # 🤖 SEND FULL CONTEXT TO AI
                    print(f"🤖 Asking Gemini AI to analyze...")
                    ai_data = extract_ai_data(
                        context["description"],
                        context["unite"],
                        context["quantity"]
                    )
                    hours_override = ai_data.get("hours_per_day", 8) if ai_data else 8
                    
                    # Step 2: Calculate Price
                    if ai_data:
                        target_value = calculate_final_price(ai_data, row_text, detected_unit, context["quantity"])

                    if target_value:
                        print(f"💰 AI Identified Price: {target_value}")
                    else:
                        print("🤷 AI could not determine price. Trying PDF extraction...")
                        
                        # LOGIC FIX: Prioritize AI unit if available, otherwise use Python unit, default to "jour"
                        unit_for_pdf = "jour" # Safe default
                        
                        if ai_data and ai_data.get("unit_type") and ai_data["unit_type"] != "unknown":
                            unit_for_pdf = ai_data["unit_type"]
                        elif detected_unit != "unknown":
                            unit_for_pdf = detected_unit
                            
                        target_value = get_price_from_zip(page, unit_for_pdf, hours_per_day=hours_override)
                        
                        if target_value:
                            print(f"📄 PDF Price Found: {target_value}")

                # 2. Fallback (If AI and PDF both fail)
                # 2. Fallback (Grand Master Logic: Precision > Broad Search)
                if not target_value:
                    # PRIORITY A: Use Unit from Python OR AI (Smart Logic v8.0)
                    unit_ai = ai_data.get("unit_type") if ai_data else None

                    if detected_unit == "jour" or unit_ai == "jour":
                        target_value = TARGET_PRICES["jour"]      # Returns 143.36 (Explicit)
                        print(f"⚠️ Fallback to Standard DAILY Price: {target_value} (Source: {'Python' if detected_unit=='jour' else 'AI'})")
                    elif detected_unit == "mois" or detected_unit == "mois_agent" or unit_ai == "mois":
                        target_value = TARGET_PRICES["mois"]      # Returns 4144.00 (Explicit)
                        print(f"⚠️ Fallback to Standard MONTHLY Price: {target_value} (Source: {'Python' if 'mois' in detected_unit else 'AI'})")
                    elif detected_unit == "heure" or unit_ai == "heure":
                        target_value = TARGET_PRICES["heure"]     # Returns 17.92 (Explicit)
                        print(f"⚠️ Fallback to Standard HOURLY Price: {target_value} (Source: {'Python' if detected_unit=='heure' else 'AI'})")
                    
                    # PRIORITY B: Broad Match (Only if Unit Column failed)
                    # REORDERED: Check 'Mois' & 'Jour' first, because 'Heure' is a common noise word
                    elif "mois" in row_text or " m " in f" {row_text} " or row_text.startswith("m"):
                        target_value = TARGET_PRICES["mois"]
                    elif "jour" in row_text or " j " in f" {row_text} " or row_text.startswith("j"):
                        target_value = TARGET_PRICES["jour"]
                    elif "heure" in row_text or " h " in f" {row_text} " or row_text.startswith("h"):
                        target_value = TARGET_PRICES["heure"]
                
                if target_value:
                    existing_price = spin.first.input_value()
                    # --- COMPETITIVE PRICING: Apply undercut ---
                    if PRICE_UNDERCUT > 0:
                        original = float(target_value)
                        target_value = str(max(round(original - PRICE_UNDERCUT, 2), 0.01))
                        print(f"💰 Competitive Price: {original} - {PRICE_UNDERCUT} = {target_value}")
                    if existing_price and existing_price.strip() == str(target_value):
                        continue 
                    
                    spin.first.scroll_into_view_if_needed()
                    spin.first.click()
                    spin.first.fill("") 
                    spin.first.type(str(target_value), delay=25)

        # Devis Generation
        btn_gen = page.get_by_role("button", name="Générer un devis")
        if btn_gen.is_visible():
            safe_click(btn_gen)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)

            # Sign 1 & 2
            # Sign 1 (Unstoppable Fix: Icon | Title | Href)
            print("🖱️ Hunting for Signer 1 button...")
            signer1 = page.locator("i.fa-signature, a[data-bs-original-title='Signer'], a[href*='/signer/']").first
            try:
                signer1.wait_for(state="visible", timeout=7000)
            except:
                print("❌ Signer 1 button NOT found after all fallback strategies.")

            if signer1.is_visible():
                # Force Click: The only way to guarantee a click on ANY screen
                signer1.click(force=True)
                print("✅ Signer 1 Clicked (Forced).")
                page.wait_for_load_state("domcontentloaded")
                
                # --- SIGNER 2 RETRY LOGIC ---
                signed_successfully = False
                attempts = 2
                
                for i in range(attempts):
                    signer2 = page.locator("#btnSigner:visible").first
                    
                    print(f"⏳ Waiting for Signer 2 button (Attempt {i+1})...")
                    try:
                        signer2.wait_for(state="visible", timeout=60000)
                    except:
                        if i < attempts - 1:
                            print("⚠️ Signer 2 button not found. Refreshing...")
                            page.reload()
                            page.wait_for_load_state("domcontentloaded")
                            continue
                        else:
                            print("❌ Signer 2 button missing after retries.")
                            return

                    if signer2.is_visible():
                        page.wait_for_timeout(1000) 
                        safe_click(signer2)
                    
                    if run_perfect_flow(timeout=30):
                        signed_successfully = True
                        break 
                    else:
                        if i < attempts - 1:
                            print("🔄 Java popup timed out (30s). Refreshing page to retry...")
                            page.reload()
                            page.wait_for_load_state("domcontentloaded")
                        else:
                            print("❌ Failed to sign after retries.")

                if signed_successfully:
                    page.bring_to_front()
                    time.sleep(1.0) 
                    
                    # FINAL SUBMISSION
                    submit_btn = page.locator("a[href*='soumettre']").first
                    submit_btn.wait_for(state="visible", timeout=15000)
                    
                    submit_btn.scroll_into_view_if_needed()
                    submit_btn.click()
                    print("📩 Submission initiated...")
                    page.wait_for_timeout(1500) 
                    
                    if submit_btn.is_visible():
                        submit_btn.click(force=True)
                        print("✅ Submission confirmed.")
                    
                    page.wait_for_load_state("networkidle")

                    try:
                        success_xpath = "//p[contains(@class, 'successFlashMessageText') and contains(text(), \"La soumission de votre offre s'est faite avec succès\")]"
                        page.wait_for_selector(f"xpath={success_xpath}", state="visible", timeout=20000)
                        print("🎉 SUCCESS CONFIRMED: La soumission de votre offre s'est faite avec succès")
                    except Exception as e:
                        print(f"⚠️ Verification Failed: Could not find success message. {e}")

    except Exception as e:
        print(f"⚠️ Error in form: {e}")

# ---------------- FILTER LOGIC ----------------
def apply_logic(page, filter_data=None):
    try:
        if USE_FILTER and filter_data:
            filter_menu = page.get_by_role("button").nth(3)
            filter_menu.wait_for(state="visible", timeout=6000)
            filter_menu.click()
            page.wait_for_timeout(500) # Give the entire filter panel a moment to animate drop-down
            
            if filter_data.get('nature_val'):
                page.get_by_label("Nature de prestation").select_option(filter_data['nature_val'])
                page.wait_for_timeout(300)
            
            if filter_data['limit_start']:
                page.get_by_role("textbox", name="Début").first.click()
                page.wait_for_timeout(300) # Give calendar UI time to render
                page.get_by_label(filter_data['limit_start']).first.click()
            
            if filter_data['limit_end']:
                page.get_by_role("textbox", name="Fin").first.click()
                page.wait_for_timeout(300) # Give calendar UI time to render
                page.get_by_label(filter_data['limit_end']).nth(1).click()
            
            if filter_data['online_start']:
                page.get_by_role("textbox", name="Début").nth(1).click()
                page.wait_for_timeout(300) # Give calendar UI time to render
                page.get_by_label(filter_data['online_start']).nth(2).click()
            
            if filter_data['online_end']:
                page.get_by_role("textbox", name="Fin").nth(1).click()
                page.wait_for_timeout(300) # Give calendar UI time to render
                page.get_by_label(filter_data['online_end']).nth(3).click()
            
            if filter_data['cat_value']:
                page.get_by_label("Catégorie principale").select_option(filter_data['cat_value'])
                page.wait_for_timeout(300)
            
            search_btn = page.get_by_role("button", name="Lancer la recherche ")
            safe_click(search_btn)
        else:
            search_btn = page.get_by_role("button", name="Lancer la recherche ")
            safe_click(search_btn)
            
        smart_wait(page, "a:has-text('Référence')")
        bookmark_url = page.url
        print(f"🔖 Bookmark URL saved: {bookmark_url}")
        return bookmark_url
    except Exception as e:
        import traceback
        print(f"⚠️ Filter logic error: {e}")
        traceback.print_exc()  # Visually show exactly which line failed in the terminal
        return page.url

# ---------------- MAIN ----------------
def run_automation():
    print("🧹 Loading previously processed tenders to prevent duplicates...")
    existing_urls = set()
    
    # Safely load history instead of wiping it
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            for line in f:
                url = line.strip()
                if url:
                    existing_urls.add(url)
        print(f"🧠 Memory Loaded: Found {len(existing_urls)} already processed tenders.")
    else:
        # Create it empty if it's the very first run
        with open(PROCESSED_FILE, "w") as f:
            pass

    filter_data = None
    if USE_FILTER:
        print("\n--- 🛠️ CONFIG ---")
        l_start = input("📅 Date limite - Début (DD/MM/YYYY): ")
        l_end   = input("📅 Date limite - Fin   (DD/MM/YYYY): ")
        o_start = input("📅 Mise en ligne - Début (DD/MM/YYYY): ")
        o_end   = input("📅 Mise en ligne - Fin (DD/MM/YYYY): ")
        cat_choice = input("📂 Category (1:Travaux, 2:Fournitures, 3:Services): ")
        nature_choice = input("🧹 Filter Nature 80? (y/n): ")

        filter_data = {
            'limit_start': get_french_date_label(l_start),
            'limit_end': get_french_date_label(l_end),
            'online_start': get_french_date_label(o_start),
            'online_end': get_french_date_label(o_end),
            'cat_value': cat_choice.strip() if cat_choice.strip() else None,
            'nature_val': '80' if nature_choice.lower() == 'y' else None
        }

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            executable_path=BRAVE_PATH,
            headless=False,
            slow_mo=40, 
            args=[f"--profile-directory={PROFILE_NAME}", "--start-maximized"],
            no_viewport=True
        )
        page = browser.new_page()
        page.on("dialog", safe_handle_dialog)

        try:
            page.goto(URL, wait_until="networkidle", timeout=60000)
            
            avis_link = page.get_by_role("link", name="Avis d'achat en cours")
            avis_link.wait_for(state="visible", timeout=10000)
            avis_link.click()
            
            login_sequence(page)
            
            page.get_by_role("link", name="Avis d'achat en cours").click()
            page.wait_for_load_state("networkidle")
            
            bookmark_url = apply_logic(page, filter_data)

            # --- PHASE 1: COLLECT URLS ---
            all_tender_urls = []
            current_page_idx = 1
            print("🔍 Starting initial URL collection phase...")
            while True:
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_selector("a:has-text('Référence')", timeout=7000)
                
                offers = page.locator("a:has-text('Référence')").all()
                if not offers: break
                
                found_on_page = 0
                for o in offers:
                    href = o.get_attribute("href")
                    if href:
                        full_url = urljoin(URL, href)
                        if full_url not in existing_urls:
                            all_tender_urls.append(full_url)
                            with open(PROCESSED_FILE, "a") as f:
                                f.write(full_url + "\n")
                            existing_urls.add(full_url)
                            found_on_page += 1
                
                print(f"📄 Page {current_page_idx}: Found {found_on_page} new tenders.")
                
                next_page_val = current_page_idx + 1
                next_btn = page.get_by_role("link", name=str(next_page_val), exact=True)
                
                if next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_load_state("networkidle")
                    current_page_idx = next_page_val
                else:
                    print("🏁 Reached last page of results.")
                    break

            print(f"📑 Total Tenders collected initially: {len(all_tender_urls)}")

            # --- PHASE 2: PROCESS URLS ---
            def process_tenders(urls):
                for idx, tender_url in enumerate(urls, 1):
                    print(f"🔗 Processing: {tender_url}")
                    try:
                        page.goto(tender_url, wait_until="domcontentloaded", timeout=30000)
                        fill_tender_form(page)
                        page.wait_for_timeout(500)
                    except Exception as e:
                        print(f"⚠️ Error on tender: {e}")
                        continue

            process_tenders(all_tender_urls)

            # --- PHASE 3: MONITORING LOOP ---
            print("\n🕒 Entering monitoring mode. Checking for new tenders every 30s...")
            while True:
                print(f"🔄 Returning to bookmark and refreshing: {datetime.now().strftime('%H:%M:%S')}")
                try:
                    page.goto(bookmark_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector("a:has-text('Référence')", timeout=7000)
                    
                    new_batch = []
                    offers = page.locator("a:has-text('Référence')").all()
                    
                    for o in offers:
                        href = o.get_attribute("href")
                        if href:
                            full_url = urljoin(URL, href)
                            if full_url not in existing_urls:
                                print(f"✨ New tender found: {full_url}")
                                new_batch.append(full_url)
                                with open(PROCESSED_FILE, "a") as f:
                                    f.write(full_url + "\n")
                                existing_urls.add(full_url)
                    
                    if new_batch:
                        print(f"🚀 Processing {len(new_batch)} newly discovered tenders...")
                        process_tenders(new_batch)
                        page.goto(bookmark_url, wait_until="domcontentloaded", timeout=30000)
                    else:
                        print("😴 No new tenders found. Waiting 30s...")
                    
                    page.wait_for_timeout(30000)
                    
                except Exception as e:
                    print(f"⚠️ Monitoring error (retrying in 10s): {e}")
                    page.wait_for_timeout(10000)

        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()

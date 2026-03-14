import os

class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "greek_chapters.db")
    VENDOR_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "greekvendorhub_refined.csv")
    COLLEGIATE_VENDOR_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ab_vendordata.csv")
    INSTITUTIONS_CSV_PATH = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "Spreadsheet Of All Colleges And Universities export 2026-03-10 12-13-20.csv",
    )
    ACCREDITED_INSTITUTIONS_CSV_PATH = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "US-Accredited-Institutions-Final.csv",
    )
    ACCREDITED_INSTITUTIONS_XLSX_PATH = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "US-Accredited-Institutions-Final.xlsx",
    )
    IPEDS_HD2024_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "hd2024.csv")
    IPEDS_EF2024A_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ef2024a.csv")
    IPEDS_IC2024_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ic2024.csv")
    IPEDS_DRVADM2024_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "drvadm2024.csv")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV", "").lower() == "production"
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")
    
    LEAD_STAGES = {"prospect", "contacted", "responded", "negotiating", "won", "lost"}
    SECURITY_QUESTIONS = [
        "What was the name of your first school?",
        "What is your mother’s maiden name?",
        "What was the name of your first pet?",
        "What is the name of the city where you were born?",
        "What is your favorite book?",
        "What is the name of your favorite teacher?",
    ]

    ORG_MAP = {
        "AKA": ("Alpha Kappa Alpha", "Sorority"),
        "APA": ("Alpha Phi Alpha", "Fraternity"),
        "DST": ("Delta Sigma Theta", "Sorority"),
        "IPT": ("Iota Phi Theta", "Fraternity"),
        "KAP": ("Kappa Alpha Psi", "Fraternity"),
        "OPP": ("Omega Psi Phi", "Fraternity"),
        "PBS": ("Phi Beta Sigma", "Fraternity"),
        "SGRHO": ("Sigma Gamma Rho", "Sorority"),
        "ZPB": ("Zeta Phi Beta", "Sorority"),
    }

    STATUS_KEYWORDS = [
        "Renamed, Reassigned",
        "Reassigned",
        "Renamed",
        "Inactive",
        "Active",
        "Dormant",
        "Reissued",
        "Revoked",
        "Closed",
        "Suspended",
    ]

    STATE_ABBR = {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
        "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
        "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
        "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
        "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
        "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
        "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
        "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    }
    US_STATES = set(STATE_ABBR.values())

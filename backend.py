import os
import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv
from supabase import create_client, Client
import json

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',') if os.getenv('AUTHORIZED_USERS') else []
API_KEY = os.getenv('API_KEY', 'rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId')
RENDER_URL = os.getenv('RENDER_URL', 'https://r6x-cyberscan-api.onrender.com')

# Supabase Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# File paths
EXE_FILENAME = "R6XScan.exe"
EXE_PATH = os.path.join(os.path.dirname(__file__), EXE_FILENAME)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

# Check if token exists
if not TOKEN:
    logger.error("❌ DISCORD_TOKEN not found in environment variables!")
    logger.error("Please set it in Render dashboard: https://dashboard.render.com")
    raise ValueError("DISCORD_TOKEN is required")

if not CHANNEL_ID:
    logger.error("❌ CHANNEL_ID not found in environment variables!")
    raise ValueError("CHANNEL_ID is required")

# Check Supabase configuration
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ SUPABASE_URL and SUPABASE_KEY must be set!")
    raise ValueError("Supabase configuration is required")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="R6X CyberScan API", 
    version="1.0.0",
    description="API for R6X CyberScan Discord Bot with Slash Commands"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== PYDANTIC MODELS ====================
class StartScanRequest(BaseModel):
    user_id: str

class StartScanResponse(BaseModel):
    scan_id: str
    bot_token: str
    channel_id: int
    message: str

class GenerateKeyRequest(BaseModel):
    user_id: str
    duration_days: Optional[int] = 30

class GenerateKeyResponse(BaseModel):
    key: str
    user_id: str
    expires_at: str
    message: str

class ScanCompleteRequest(BaseModel):
    scan_id: str
    user_id: str
    files_scanned: int
    suspicious_count: int
    duration: float
    logitech: Optional[Dict[str, Any]] = None

class ScanResponse(BaseModel):
    status: str
    message: str
    scan_id: Optional[str] = None

# ==================== SUPABASE KEY MANAGEMENT ====================
class KeyManager:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
    
    def generate_key(self, user_id: str, duration_days: int = 30) -> str:
        """Generate a unique key for a user and store in Supabase"""
        # Generate a random key (format: R6X-XXXXX-XXXXX-XXXXX)
        alphabet = string.ascii_uppercase + string.digits
        part1 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part2 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part3 = ''.join(secrets.choice(alphabet) for _ in range(5))
        key = f"R6X-{part1}-{part2}-{part3}"
        
        # Calculate expiration
        expires_at = (datetime.now() + timedelta(days=duration_days)).isoformat()
        
        # Store in Supabase
        data = {
            'key': key,
            'user_id': user_id,
            'created_at': datetime.now().isoformat(),
            'expires_at': expires_at,
            'used': False,
            'duration_days': duration_days
        }
        
        result = self.supabase.table('keys').insert(data).execute()
        
        if not result.data:
            raise Exception("Failed to insert key into Supabase")
        
        logger.info(f"✅ Generated key {key} for user {user_id} (expires in {duration_days} days)")
        return key
    
    def validate_key(self, key: str, user_id: str) -> tuple:
        """Validate if a key is valid for a user using Supabase"""
        # Query the key
        result = self.supabase.table('keys').select('*').eq('key', key).execute()
        
        if not result.data or len(result.data) == 0:
            return False, "Key not found"
        
        key_data = result.data[0]
        
        # Check if key belongs to user
        if key_data['user_id'] != user_id:
            return False, f"Key belongs to user {key_data['user_id']}"
        
        # Check if already used
        if key_data['used']:
            return False, "Key has already been used"
        
        # Check expiration
        expires_at = datetime.fromisoformat(key_data['expires_at'].replace('Z', '+00:00'))
        if datetime.now() > expires_at:
            return False, "Key has expired"
        
        return True, "Key is valid"
    
    def mark_key_used(self, key: str):
        """Mark a key as used in Supabase"""
        result = self.supabase.table('keys').update({'used': True, 'used_at': datetime.now().isoformat()}).eq('key', key).execute()
        
        if result.data:
            logger.info(f"✅ Key {key} marked as used")
            return True
        return False
    
    def get_user_keys(self, user_id: str) -> List[Dict]:
        """Get all keys for a user from Supabase"""
        result = self.supabase.table('keys').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        
        keys = []
        for key_data in result.data or []:
            # Check if valid
            expires_at = datetime.fromisoformat(key_data['expires_at'].replace('Z', '+00:00'))
            key_data['valid'] = not key_data['used'] and datetime.now() <= expires_at
            keys.append(key_data)
        
        return keys
    
    def get_stats(self) -> Dict:
        """Get key statistics from Supabase"""
        # Get all keys
        result = self.supabase.table('keys').select('*').execute()
        keys = result.data or []
        
        total_keys = len(keys)
        used_keys = sum(1 for k in keys if k['used'])
        
        # Count valid keys (not used and not expired)
        valid_keys = 0
        users = set()
        
        for k in keys:
            users.add(k['user_id'])
            expires_at = datetime.fromisoformat(k['expires_at'].replace('Z', '+00:00'))
            if not k['used'] and datetime.now() <= expires_at:
                valid_keys += 1
        
        return {
            'total_keys': total_keys,
            'used_keys': used_keys,
            'valid_keys': valid_keys,
            'unique_users': len(users)
        }

# Initialize key manager with Supabase
key_manager = KeyManager(supabase)

# ==================== DATA STORAGE (Supabase) ====================
def create_tables_if_not_exist():
    """Create necessary tables in Supabase if they don't exist"""
    try:
        # This is handled by Supabase migrations - we'll just check if we can access
        logger.info("✅ Connected to Supabase")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Supabase: {e}")
        raise

# Call this on startup
create_tables_if_not_exist()

# In-memory cache for active scans (still needed for real-time)
active_scans = {}
scan_history_cache = []
bot_start_time = datetime.now()

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "R6X CyberScan API",
        "version": "1.0.0",
        "status": "online",
        "database": "Supabase",
        "key_system": "active",
        "slash_commands": [
            "/generate_key [user_id] [days] - Generate a new license key",
            "/list_keys [user_id] - List all keys for a user",
            "/validate_key [user_id] - Check if user has valid key",
            "/stats - Show bot statistics"
        ],
        "endpoints": {
            "/health": "Health check",
            "/api/generate-key": "Generate a new key for a user (API)",
            "/api/validate-key": "Validate a key",
            "/api/start-scan": "Start a new scan (requires valid key)",
            "/api/scan/complete": "Mark scan as complete",
            "/api/user/keys/{user_id}": "Get user's keys",
            "/api/stats": "Get statistics",
            "/download-exe": "Download R6XScan.exe"
        }
    }

@app.post("/api/generate-key", response_model=GenerateKeyResponse)
async def generate_key(request: GenerateKeyRequest, x_api_key: Optional[str] = Header(None)):
    """Generate a new key for a user (admin only)"""
    # Check API key (admin access)
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt for key generation: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    duration_days = request.duration_days
    
    logger.info(f"Key generation requested for user: {user_id} (duration: {duration_days} days)")
    
    # Generate key and store in Supabase
    key = key_manager.generate_key(user_id, duration_days)
    
    # Get expiration
    keys = key_manager.get_user_keys(user_id)
    key_data = next((k for k in keys if k['key'] == key), None)
    expires_at = key_data['expires_at'] if key_data else datetime.now().isoformat()
    
    return GenerateKeyResponse(
        key=key,
        user_id=user_id,
        expires_at=expires_at,
        message=f"Key generated successfully. Valid for {duration_days} days."
    )

@app.post("/api/validate-key")
async def validate_key(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Validate if a user has a valid key"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    
    # Get user's keys from Supabase
    user_keys = key_manager.get_user_keys(user_id)
    
    # Check if any valid key exists
    valid_keys = [k for k in user_keys if k['valid']]
    
    if valid_keys:
        return {
            'valid': True,
            'user_id': user_id,
            'available_keys': len(valid_keys),
            'message': f"User has {len(valid_keys)} valid key(s)"
        }
    else:
        return {
            'valid': False,
            'user_id': user_id,
            'message': "No valid keys found for this user"
        }

@app.post("/api/start-scan", response_model=StartScanResponse)
async def start_scan(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Start a new scan (requires valid key)"""
    # Check API key
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    logger.info(f"Start scan requested for user_id: {user_id}")
    
    # Check if user has a valid key
    user_keys = key_manager.get_user_keys(user_id)
    valid_key = None
    
    for key_info in user_keys:
        if key_info['valid']:
            valid_key = key_info['key']
            break
    
    if not valid_key:
        logger.warning(f"User {user_id} attempted scan without valid key")
        raise HTTPException(
            status_code=403, 
            detail="No valid key found. Please generate a key first using /generate_key command"
        )
    
    # Mark key as used
    key_manager.mark_key_used(valid_key)
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id[-8:]}"
    
    # Store scan session in memory (temporary)
    active_scans[scan_id] = {
        'user_id': user_id,
        'start_time': datetime.now(),
        'status': 'pending',
        'bot_token': TOKEN,
        'channel_id': CHANNEL_ID,
        'key_used': valid_key
    }
    
    logger.info(f"✅ Scan started: {scan_id} for user {user_id} (key: {valid_key})")
    
    # Return the bot token and channel ID to the scanner
    return StartScanResponse(
        scan_id=scan_id,
        bot_token=TOKEN,
        channel_id=CHANNEL_ID,
        message=f"Scan started successfully using key {valid_key}. Bot will run locally with slash commands."
    )

@app.post("/api/scan/complete")
async def scan_complete(request: ScanCompleteRequest, x_api_key: Optional[str] = Header(None)):
    """Mark scan as complete and store results in Supabase"""
    # Check API key
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = request.scan_id
    user_id = request.user_id
    
    logger.info(f"Scan complete received for scan_id: {scan_id}")
    
    # Check if scan exists in memory
    if scan_id not in active_scans:
        logger.warning(f"Scan ID not found: {scan_id}")
        raise HTTPException(status_code=404, detail="Invalid or expired scan ID")
    
    # Verify user matches
    if active_scans[scan_id]['user_id'] != user_id:
        logger.warning(f"User mismatch for scan {scan_id}")
        raise HTTPException(status_code=403, detail="User mismatch")
    
    # Update scan status in memory
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    active_scans[scan_id]['data'] = request.dict()
    
    # Store scan in Supabase
    scan_data = {
        'scan_id': scan_id,
        'user_id': user_id,
        'completed_time': datetime.now().isoformat(),
        'files_scanned': request.files_scanned,
        'suspicious_count': request.suspicious_count,
        'duration': request.duration,
        'key_used': active_scans[scan_id].get('key_used'),
        'logitech': json.dumps(request.logitech) if request.logitech else None
    }
    
    supabase.table('scans').insert(scan_data).execute()
    
    # Add to history cache
    scan_history_cache.append(scan_data)
    
    # Keep only last 100 scans in cache
    while len(scan_history_cache) > 100:
        scan_history_cache.pop(0)
    
    logger.info(f"✅ Scan completed: {scan_id}")
    
    return ScanResponse(
        status='success',
        message='Scan marked as complete',
        scan_id=scan_id
    )

@app.get("/api/user/keys/{user_id}")
async def get_user_keys(user_id: str, x_api_key: Optional[str] = Header(None)):
    """Get all keys for a specific user from Supabase"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    keys = key_manager.get_user_keys(user_id)
    return {
        'user_id': user_id,
        'total_keys': len(keys),
        'keys': keys
    }

@app.get("/api/stats")
async def get_stats(x_api_key: Optional[str] = Header(None)):
    """Get overall statistics"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Get scan stats from Supabase
    scans_result = supabase.table('scans').select('*').execute()
    scans = scans_result.data or []
    
    total_scans = len(scans)
    total_files = sum(s.get('files_scanned', 0) for s in scans)
    total_suspicious = sum(s.get('suspicious_count', 0) for s in scans)
    
    avg_duration = 0
    if total_scans > 0:
        avg_duration = sum(s.get('duration', 0) for s in scans) / total_scans
    
    # Get key stats
    key_stats = key_manager.get_stats()
    
    return {
        'total_scans': total_scans,
        'total_files_scanned': total_files,
        'total_suspicious_files': total_suspicious,
        'average_duration': avg_duration,
        'active_scans': len(active_scans),
        'key_stats': key_stats
    }

@app.get("/api/scans/recent")
async def get_recent_scans(limit: int = 10, x_api_key: Optional[str] = Header(None)):
    """Get recent completed scans from Supabase"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    result = supabase.table('scans').select('*').order('completed_time', desc=True).limit(limit).execute()
    return {'recent_scans': result.data or []}

@app.get("/download-exe")
async def download_exe():
    """Download the R6XScan.exe file"""
    if not os.path.exists(EXE_PATH):
        # Try to find it in the current directory
        alt_path = os.path.join(os.getcwd(), EXE_FILENAME)
        if os.path.exists(alt_path):
            return FileResponse(
                path=alt_path,
                filename=EXE_FILENAME,
                media_type="application/octet-stream"
            )
        
        logger.error(f"EXE not found at {EXE_PATH}")
        raise HTTPException(
            status_code=404, 
            detail=f"EXE not found. Please ensure {EXE_FILENAME} is in the same directory as the bot."
        )
    
    return FileResponse(
        path=EXE_PATH,
        filename=EXE_FILENAME,
        media_type="application/octet-stream"
    )

@app.get("/health")
async def health():
    """Health check endpoint"""
    # Check Supabase connection
    try:
        supabase.table('keys').select('count', count='exact').limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    # Get key stats
    key_stats = key_manager.get_stats()
    
    return {
        'status': 'healthy',
        'database': {
            'type': 'Supabase',
            'status': db_status
        },
        'key_system': key_stats,
        'active_scans': len(active_scans),
        'total_scans_completed': len(scan_history_cache),
        'authorized_users': len(AUTHORIZED_USERS) if AUTHORIZED_USERS != [''] else 0,
        'exe_available': os.path.exists(EXE_PATH),
        'token_loaded': bool(TOKEN),
        'channel_configured': bool(CHANNEL_ID),
        'uptime': str(datetime.now() - bot_start_time).split('.')[0]
    }

@app.get("/api/scan/status/{scan_id}")
async def get_scan_status(scan_id: str, x_api_key: Optional[str] = Header(None)):
    """Get status of a specific scan"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Check in-memory active scans first
    if scan_id in active_scans:
        scan = active_scans[scan_id]
        return {
            'scan_id': scan_id,
            'user_id': scan['user_id'],
            'status': scan['status'],
            'start_time': scan['start_time'].isoformat(),
            'completed_time': scan.get('completed_time', '').isoformat() if scan.get('completed_time') else None,
            'key_used': scan.get('key_used')
        }
    
    # Check Supabase for completed scans
    result = supabase.table('scans').select('*').eq('scan_id', scan_id).execute()
    if result.data and len(result.data) > 0:
        scan = result.data[0]
        return {
            'scan_id': scan_id,
            'user_id': scan['user_id'],
            'status': 'completed',
            'completed_time': scan['completed_time'],
            'files_scanned': scan['files_scanned'],
            'suspicious_count': scan['suspicious_count'],
            'duration': scan['duration'],
            'key_used': scan.get('key_used'),
            'logitech': json.loads(scan['logitech']) if scan.get('logitech') else None
        }
    
    raise HTTPException(status_code=404, detail="Scan ID not found")

# ==================== RUN FASTAPI ====================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting R6X CyberScan API on port {port}")
    logger.info(f"Database: Supabase")
    logger.info(f"Authorized users: {AUTHORIZED_USERS}")
    logger.info(f"Channel ID: {CHANNEL_ID}")
    logger.info(f"Token loaded: {bool(TOKEN)}")
    logger.info(f"Key generation system: ACTIVE")
    logger.info(f"Slash commands supported: /generate_key, /list_keys, /validate_key, /stats")
    
    uvicorn.run(app, host="0.0.0.0", port=port)

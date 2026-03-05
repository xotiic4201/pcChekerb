import os
import logging
import secrets
import string
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')  # The Discord bot token for scanners to use
CHANNEL_ID = os.getenv('CHANNEL_ID', '0')  # Default channel for scan results
API_KEY = os.getenv('API_KEY', 'rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId')
RENDER_URL = os.getenv('RENDER_URL', 'https://pcchekerb.onrender.com')

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

# Check if bot token exists
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables!")
    raise ValueError("BOT_TOKEN is required")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="R6X CyberScan API", 
    version="1.0.0",
    description="API for R6X CyberScan Scanner"
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
class LoginRequest(BaseModel):
    user_id: str

class LoginResponse(BaseModel):
    success: bool
    scan_id: Optional[str] = None
    message: str

class BotTokenResponse(BaseModel):
    bot_token: str
    channel_id: int
    message: str

class ScanCompleteRequest(BaseModel):
    scan_id: str
    user_id: str
    files_scanned: int
    suspicious_count: int
    duration: float
    logitech: Optional[Dict[str, Any]] = None

class GenerateKeyRequest(BaseModel):
    user_id: str
    duration_days: Optional[int] = 30

class GenerateKeyResponse(BaseModel):
    key: str
    user_id: str
    expires_at: str
    message: str

# ==================== KEY MANAGEMENT ====================
class KeyManager:
    def __init__(self):
        self.keys = {}
        self.user_keys = {}
        self.keys_file = os.path.join(os.path.dirname(__file__), 'keys.json')
        self.load_keys()
    
    def load_keys(self):
        """Load keys from file if exists"""
        if os.path.exists(self.keys_file):
            try:
                with open(self.keys_file, 'r') as f:
                    data = json.load(f)
                    self.keys = data.get('keys', {})
                    self.user_keys = data.get('user_keys', {})
                    logger.info(f"✅ Loaded {len(self.keys)} keys from file")
            except Exception as e:
                logger.error(f"Failed to load keys: {e}")
    
    def save_keys(self):
        """Save keys to file"""
        try:
            with open(self.keys_file, 'w') as f:
                json.dump({
                    'keys': self.keys,
                    'user_keys': self.user_keys
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save keys: {e}")
    
    def generate_key(self, user_id: str, duration_days: int = 30) -> str:
        """Generate a unique key for a user"""
        alphabet = string.ascii_uppercase + string.digits
        part1 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part2 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part3 = ''.join(secrets.choice(alphabet) for _ in range(5))
        key = f"R6X-{part1}-{part2}-{part3}"
        
        expires_at = (datetime.now() + timedelta(days=duration_days)).timestamp()
        
        self.keys[key] = {
            'user_id': user_id,
            'expires_at': expires_at,
            'used': False,
            'created_at': datetime.now().isoformat(),
            'duration_days': duration_days
        }
        
        if user_id not in self.user_keys:
            self.user_keys[user_id] = []
        self.user_keys[user_id].append(key)
        
        self.save_keys()
        logger.info(f"✅ Generated key {key} for user {user_id}")
        return key
    
    def validate_user(self, user_id: str) -> tuple:
        """Check if user has any valid key and return one to use"""
        if user_id not in self.user_keys:
            return False, "No keys found for this user", None
        
        # Find first valid key
        for key in self.user_keys[user_id]:
            if key in self.keys:
                key_data = self.keys[key]
                if not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']:
                    # Mark as used
                    self.keys[key]['used'] = True
                    self.keys[key]['used_at'] = datetime.now().isoformat()
                    self.save_keys()
                    logger.info(f"✅ Key {key} marked as used for scan")
                    return True, "Valid key found and used", key
        
        return False, "No valid keys found (all used or expired)", None
    
    def get_stats(self) -> Dict:
        """Get key statistics"""
        total_keys = len(self.keys)
        used_keys = sum(1 for k in self.keys.values() if k.get('used', False))
        valid_keys = sum(1 for k in self.keys.values() 
                        if not k.get('used', False) and datetime.now().timestamp() <= k['expires_at'])
        
        return {
            'total_keys': total_keys,
            'used_keys': used_keys,
            'valid_keys': valid_keys,
            'unique_users': len(self.user_keys)
        }

# Initialize key manager
key_manager = KeyManager()

# ==================== DATA STORAGE ====================
active_scans = {}
scan_history = []
bot_start_time = datetime.now()

# ==================== API ROUTES ====================

@app.get("/")
async def root():
    return {
        "name": "R6X CyberScan API",
        "version": "1.0.0",
        "status": "online",
        "endpoints": {
            "/health": "Health check",
            "/api/bot-token": "Get bot token for scanner (no auth needed)",
            "/api/login": "Login with Discord ID (validates key)",
            "/api/scan/complete": "Mark scan as complete",
            "/api/generate-key": "Generate a new key (admin only)",
            "/api/stats": "Get statistics"
        }
    }

@app.get("/api/bot-token", response_model=BotTokenResponse)
async def get_bot_token(x_api_key: Optional[str] = Header(None)):
    """Get bot token for scanner to start Discord bot (no user ID needed)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    logger.info("✅ Bot token requested by scanner")
    
    return BotTokenResponse(
        bot_token=BOT_TOKEN,
        channel_id=int(CHANNEL_ID),
        message="Bot token retrieved successfully"
    )

@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest, x_api_key: Optional[str] = Header(None)):
    """Login user with Discord ID - validates key and starts scan session"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    logger.info(f"🔐 Login attempt for user: {user_id}")
    
    # Validate user has a valid key and use it
    valid, message, used_key = key_manager.validate_user(user_id)
    
    if not valid:
        logger.warning(f"❌ Login failed for user {user_id}: {message}")
        return LoginResponse(
            success=False,
            message=message
        )
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id[-8:]}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': user_id,
        'start_time': datetime.now(),
        'status': 'active',
        'key_used': used_key
    }
    
    logger.info(f"✅ Login successful for user {user_id} - Scan ID: {scan_id} (key: {used_key})")
    
    return LoginResponse(
        success=True,
        scan_id=scan_id,
        message=f"Login successful. Scan ID: {scan_id}"
    )

@app.post("/api/scan/complete")
async def scan_complete(request: ScanCompleteRequest, x_api_key: Optional[str] = Header(None)):
    """Mark scan as complete"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = request.scan_id
    user_id = request.user_id
    
    if scan_id not in active_scans:
        logger.warning(f"❌ Scan complete failed - invalid scan ID: {scan_id}")
        raise HTTPException(status_code=404, detail="Invalid scan ID")
    
    if active_scans[scan_id]['user_id'] != user_id:
        logger.warning(f"❌ Scan complete failed - user mismatch for scan {scan_id}")
        raise HTTPException(status_code=403, detail="User mismatch")
    
    # Update scan status
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    active_scans[scan_id]['data'] = request.dict()
    
    # Add to history
    scan_history.append({
        'scan_id': scan_id,
        'user_id': user_id,
        'completed_time': datetime.now().isoformat(),
        'files_scanned': request.files_scanned,
        'suspicious_count': request.suspicious_count,
        'duration': request.duration,
        'key_used': active_scans[scan_id].get('key_used'),
        'logitech': request.logitech
    })
    
    logger.info(f"✅ Scan completed: {scan_id} - Files: {request.files_scanned}, Suspicious: {request.suspicious_count}")
    
    return {"status": "success", "message": "Scan marked as complete"}

@app.post("/api/generate-key", response_model=GenerateKeyResponse)
async def generate_key(request: GenerateKeyRequest, x_api_key: Optional[str] = Header(None)):
    """Generate a new key for a user (admin only - for Discord bot)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    duration_days = request.duration_days
    
    logger.info(f"🔑 Key generation requested for user: {user_id} (duration: {duration_days} days)")
    
    # Generate key
    key = key_manager.generate_key(user_id, duration_days)
    
    # Get expiration date
    key_data = key_manager.keys[key]
    expires_at = datetime.fromtimestamp(key_data['expires_at']).isoformat()
    
    return GenerateKeyResponse(
        key=key,
        user_id=user_id,
        expires_at=expires_at,
        message=f"Key generated successfully. Valid for {duration_days} days."
    )

@app.get("/api/stats")
async def get_stats(x_api_key: Optional[str] = Header(None)):
    """Get overall statistics"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    total_scans = len(scan_history)
    total_files = sum(s.get('files_scanned', 0) for s in scan_history)
    total_suspicious = sum(s.get('suspicious_count', 0) for s in scan_history)
    
    avg_duration = 0
    if total_scans > 0:
        avg_duration = sum(s.get('duration', 0) for s in scan_history) / total_scans
    
    key_stats = key_manager.get_stats()
    
    return {
        'total_scans': total_scans,
        'total_files_scanned': total_files,
        'total_suspicious_files': total_suspicious,
        'average_duration': avg_duration,
        'active_scans': len(active_scans),
        'key_stats': key_stats
    }

@app.get("/api/scan/status/{scan_id}")
async def get_scan_status(scan_id: str, x_api_key: Optional[str] = Header(None)):
    """Get status of a specific scan"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
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
    
    # Check history
    for scan in scan_history:
        if scan['scan_id'] == scan_id:
            return {
                'scan_id': scan_id,
                'user_id': scan['user_id'],
                'status': 'completed',
                'completed_time': scan['completed_time'],
                'files_scanned': scan['files_scanned'],
                'suspicious_count': scan['suspicious_count'],
                'duration': scan['duration'],
                'key_used': scan.get('key_used')
            }
    
    raise HTTPException(status_code=404, detail="Scan ID not found")

@app.get("/api/scans/recent")
async def get_recent_scans(limit: int = 10, x_api_key: Optional[str] = Header(None)):
    """Get recent completed scans"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    recent = scan_history[-limit:] if scan_history else []
    return {'recent_scans': recent}

@app.get("/health")
async def health():
    """Health check endpoint"""
    key_stats = key_manager.get_stats()
    
    return {
        'status': 'healthy',
        'key_system': key_stats,
        'active_scans': len(active_scans),
        'total_scans_completed': len(scan_history),
        'bot_token_configured': bool(BOT_TOKEN),
        'channel_id_configured': bool(CHANNEL_ID),
        'uptime': str(datetime.now() - bot_start_time).split('.')[0]
    }

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🚀 Starting R6X CyberScan API on port {port}")
    logger.info(f"📊 Key system loaded: {key_manager.get_stats()['total_keys']} total keys")
    logger.info(f"🤖 Bot token configured: {bool(BOT_TOKEN)}")
    logger.info(f"📢 Channel ID: {CHANNEL_ID}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)

import os
import logging
import secrets
import string
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
API_KEY = os.getenv('API_KEY', 'rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId')
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Token for the scanner to use
CHANNEL_ID = os.getenv('CHANNEL_ID', '0')

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

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
class StartScanRequest(BaseModel):
    user_id: str

class StartScanResponse(BaseModel):
    scan_id: str
    bot_token: str
    channel_id: int
    message: str

class ValidateKeyRequest(BaseModel):
    user_id: str

class ValidateKeyResponse(BaseModel):
    valid: bool
    message: str
    available_keys: Optional[int] = None

class ScanCompleteRequest(BaseModel):
    scan_id: str
    user_id: str
    files_scanned: int
    suspicious_count: int
    duration: float

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
        """Check if user has any valid key"""
        if user_id not in self.user_keys:
            return False, "No keys found for this user", 0
        
        valid_keys = []
        for key in self.user_keys[user_id]:
            if key in self.keys:
                key_data = self.keys[key]
                if not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']:
                    valid_keys.append(key)
        
        if valid_keys:
            return True, f"User has {len(valid_keys)} valid key(s)", len(valid_keys)
        else:
            return False, "No valid keys found (all used or expired)", 0
    
    def use_one_key(self, user_id: str) -> Optional[str]:
        """Use one valid key for a user (returns the key used)"""
        if user_id not in self.user_keys:
            return None
        
        for key in self.user_keys[user_id]:
            if key in self.keys:
                key_data = self.keys[key]
                if not key_data['used'] and datetime.now().timestamp() <= key_data['expires_at']:
                    self.keys[key]['used'] = True
                    self.keys[key]['used_at'] = datetime.now().isoformat()
                    self.save_keys()
                    logger.info(f"✅ Key {key} marked as used")
                    return key
        
        return None
    
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
            "/api/validate-key": "Check if user has a valid key",
            "/api/start-scan": "Start a new scan (returns bot token)",
            "/api/scan/complete": "Mark scan as complete"
        }
    }

@app.post("/api/validate-key", response_model=ValidateKeyResponse)
async def validate_key(request: ValidateKeyRequest, x_api_key: Optional[str] = Header(None)):
    """Check if a user has a valid key"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    valid, message, count = key_manager.validate_user(request.user_id)
    
    return ValidateKeyResponse(
        valid=valid,
        message=message,
        available_keys=count if valid else None
    )

@app.post("/api/start-scan", response_model=StartScanResponse)
async def start_scan(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Start a new scan - returns bot token and channel ID"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    
    # Validate user has a key
    valid, message, count = key_manager.validate_user(user_id)
    if not valid:
        raise HTTPException(status_code=403, detail=message)
    
    # Use one key
    used_key = key_manager.use_one_key(user_id)
    if not used_key:
        raise HTTPException(status_code=403, detail="Failed to use key")
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id[-8:]}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': user_id,
        'start_time': datetime.now(),
        'status': 'pending',
        'key_used': used_key
    }
    
    logger.info(f"✅ Scan started: {scan_id} for user {user_id} (key: {used_key})")
    
    # Return bot token and channel ID for the scanner to use
    return StartScanResponse(
        scan_id=scan_id,
        bot_token=BOT_TOKEN,
        channel_id=int(CHANNEL_ID),
        message=f"Scan started successfully. Bot will run locally."
    )

@app.post("/api/scan/complete")
async def scan_complete(request: ScanCompleteRequest, x_api_key: Optional[str] = Header(None)):
    """Mark scan as complete"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = request.scan_id
    user_id = request.user_id
    
    if scan_id not in active_scans:
        raise HTTPException(status_code=404, detail="Invalid scan ID")
    
    if active_scans[scan_id]['user_id'] != user_id:
        raise HTTPException(status_code=403, detail="User mismatch")
    
    # Update scan status
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    
    # Add to history
    scan_history.append({
        'scan_id': scan_id,
        'user_id': user_id,
        'completed_time': datetime.now().isoformat(),
        'files_scanned': request.files_scanned,
        'suspicious_count': request.suspicious_count,
        'duration': request.duration,
        'key_used': active_scans[scan_id].get('key_used')
    })
    
    logger.info(f"✅ Scan completed: {scan_id}")
    
    return {"status": "success", "message": "Scan marked as complete"}

@app.get("/health")
async def health():
    """Health check"""
    key_stats = key_manager.get_stats()
    
    return {
        'status': 'healthy',
        'key_system': key_stats,
        'active_scans': len(active_scans),
        'total_scans_completed': len(scan_history),
        'uptime': str(datetime.now() - bot_start_time).split('.')[0]
    }

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting R6X CyberScan API on port {port}")
    logger.info(f"Key system loaded: {key_manager.get_stats()['total_keys']} keys")
    
    uvicorn.run(app, host="0.0.0.0", port=port)

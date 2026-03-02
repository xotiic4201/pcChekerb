"""
R6XInspector Backend API - FastAPI Version
Complete backend with token delivery and user authentication
"""

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
import hashlib
import hmac
import time
import logging
import secrets
import json
from typing import Optional, Dict, List
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="R6XInspector Backend API",
    description="Secure token delivery and user authentication for R6X CYBERSCAN",
    version="4.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CONFIGURATION ====================
# Get from environment variables
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
API_KEY = os.environ.get('API_KEY')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'production')

# Admin credentials (stored in environment variables)
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'xotiic')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')  # SHA256 hash of password

# User database (in production, use a real database)
# For now, we'll use environment variables for users
# Format: USER_1_USERNAME, USER_1_PASSWORD_HASH, USER_2_USERNAME, USER_2_PASSWORD_HASH, etc.
USERS: Dict[str, Dict] = {}

# Load users from environment variables
for i in range(1, 10):  # Support up to 10 users
    username = os.environ.get(f'USER_{i}_USERNAME')
    password_hash = os.environ.get(f'USER_{i}_PASSWORD_HASH')
    if username and password_hash:
        USERS[username] = {
            'password_hash': password_hash,
            'is_admin': False,
            'created_at': time.time()
        }
        logger.info(f"Loaded user: {username}")

# Add admin to users if not already there
if ADMIN_USERNAME and ADMIN_PASSWORD_HASH:
    USERS[ADMIN_USERNAME] = {
        'password_hash': ADMIN_PASSWORD_HASH,
        'is_admin': True,
        'created_at': time.time()
    }
    logger.info(f"Loaded admin user: {ADMIN_USERNAME}")

# Token cache
token_cache = {
    'token': None,
    'expires': 0,
    'signature': None,
    'timestamp': 0
}

# Session store (in production, use Redis)
sessions: Dict[str, Dict] = {}

# Scan results store (for admin to view all scans)
scan_results: List[Dict] = []

# ==================== MODELS ====================
class TokenResponse(BaseModel):
    token: str
    timestamp: int
    expires: int
    signature: str
    nonce: str

class StatusResponse(BaseModel):
    online: bool
    token_configured: bool
    timestamp: int
    environment: str
    version: str
    users_configured: int

class HealthResponse(BaseModel):
    status: str
    timestamp: int
    version: str

class LoginRequest(BaseModel):
    username: str
    password_hash: str
    token: str
    client_id: Optional[str] = None

class LoginResponse(BaseModel):
    success: bool
    is_admin: bool
    message: str
    session_token: Optional[str] = None
    expires_in: Optional[int] = None

class VerifyRequest(BaseModel):
    session_token: str

class VerifyResponse(BaseModel):
    valid: bool
    username: str
    is_admin: bool
    expires_in: Optional[int] = None

class ScanResultSubmission(BaseModel):
    session_token: str
    scan_data: Dict
    username: str
    computer_name: str
    timestamp: int

class ScanResultResponse(BaseModel):
    success: bool
    message: str
    scan_id: Optional[str] = None

class AdminScansResponse(BaseModel):
    scans: List[Dict]
    total: int
    timestamp: int

# ==================== MIDDLEWARE ====================
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# ==================== HELPER FUNCTIONS ====================
def create_session(username: str, is_admin: bool) -> str:
    """Create a new session token"""
    session_token = secrets.token_urlsafe(32)
    expires = time.time() + 86400  # 24 hours
    
    sessions[session_token] = {
        'username': username,
        'is_admin': is_admin,
        'created_at': time.time(),
        'expires_at': expires,
        'last_activity': time.time()
    }
    
    return session_token

def verify_session(session_token: str) -> Optional[Dict]:
    """Verify a session token"""
    if session_token not in sessions:
        return None
    
    session = sessions[session_token]
    if time.time() > session['expires_at']:
        del sessions[session_token]
        return None
    
    # Update last activity
    session['last_activity'] = time.time()
    return session

def cleanup_sessions():
    """Remove expired sessions"""
    current_time = time.time()
    expired = [token for token, session in sessions.items() 
               if current_time > session['expires_at']]
    for token in expired:
        del sessions[token]

# ==================== API ENDPOINTS ====================

@app.get("/", response_model=StatusResponse)
async def root():
    """Root endpoint - shows API status"""
    cleanup_sessions()
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "4.0.0",
        "users_configured": len(USERS)
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "version": "4.0.0"
    }

@app.get("/api/status", response_model=StatusResponse)
async def api_status():
    """API status endpoint"""
    cleanup_sessions()
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "4.0.0",
        "users_configured": len(USERS)
    }

@app.get("/api/token", response_model=TokenResponse)
async def get_token(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """
    Get Discord bot token with proper signature
    """
    logger.info("Token request received")
    
    # Verify API key
    if not x_api_key:
        logger.warning("Missing API key")
        raise HTTPException(status_code=401, detail="X-API-Key header is required")
    
    if not API_KEY or x_api_key != API_KEY:
        logger.warning("Invalid API key")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Check if token is configured
    if not DISCORD_TOKEN:
        logger.error("Discord token not configured")
        raise HTTPException(status_code=503, detail="Discord token not configured")
    
    # Check cache
    current_time = int(time.time())
    if token_cache['token'] and current_time < token_cache['expires']:
        logger.info("Returning cached token")
        return {
            "token": token_cache['token'],
            "timestamp": token_cache['timestamp'],
            "expires": token_cache['expires'] - current_time,
            "signature": token_cache['signature'],
            "nonce": token_cache.get('nonce', '')
        }
    
    # Generate new token
    expires_in = 3600  # 1 hour
    timestamp = current_time
    nonce = secrets.token_hex(8)
    
    # Create signature
    signature_payload = f"{DISCORD_TOKEN}:{timestamp}:{expires_in}:{nonce}"
    signature = hmac.new(
        API_KEY.encode('utf-8'),
        signature_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Update cache
    token_cache.update({
        'token': DISCORD_TOKEN,
        'timestamp': timestamp,
        'expires': timestamp + expires_in,
        'signature': signature,
        'nonce': nonce
    })
    
    logger.info("Token generated successfully")
    
    return {
        "token": DISCORD_TOKEN,
        "timestamp": timestamp,
        "expires": expires_in,
        "signature": signature,
        "nonce": nonce
    }

@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user and create session
    """
    logger.info(f"Login attempt for user: {request.username}")
    
    # Verify token first
    if not request.token or request.token != DISCORD_TOKEN:
        logger.warning("Invalid token in login request")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Invalid authentication token"
        )
    
    # Check if user exists
    if request.username not in USERS:
        logger.warning(f"User not found: {request.username}")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Invalid username or password"
        )
    
    # Verify password
    user_data = USERS[request.username]
    if not hmac.compare_digest(request.password_hash, user_data['password_hash']):
        logger.warning(f"Invalid password for user: {request.username}")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Invalid username or password"
        )
    
    # Create session
    session_token = create_session(request.username, user_data['is_admin'])
    
    logger.info(f"Login successful for user: {request.username} (admin: {user_data['is_admin']})")
    
    return LoginResponse(
        success=True,
        is_admin=user_data['is_admin'],
        message="Login successful",
        session_token=session_token,
        expires_in=86400
    )

@app.post("/api/verify", response_model=VerifyResponse)
async def verify(request: VerifyRequest):
    """
    Verify session token
    """
    session = verify_session(request.session_token)
    
    if not session:
        return VerifyResponse(
            valid=False,
            username="",
            is_admin=False
        )
    
    expires_in = int(session['expires_at'] - time.time())
    
    return VerifyResponse(
        valid=True,
        username=session['username'],
        is_admin=session['is_admin'],
        expires_in=expires_in
    )

@app.post("/api/logout")
async def logout(request: VerifyRequest):
    """
    Logout - invalidate session
    """
    if request.session_token in sessions:
        del sessions[request.session_token]
    
    return {"success": True, "message": "Logged out successfully"}

@app.post("/api/submit-scan", response_model=ScanResultResponse)
async def submit_scan(request: ScanResultSubmission):
    """
    Submit scan results (for users)
    """
    # Verify session
    session = verify_session(request.session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    # Store scan result
    scan_id = secrets.token_hex(8)
    scan_entry = {
        'scan_id': scan_id,
        'username': request.username,
        'computer_name': request.computer_name,
        'timestamp': request.timestamp,
        'datetime': datetime.fromtimestamp(request.timestamp).isoformat(),
        'data': request.scan_data,
        'submitted_by': session['username']
    }
    
    scan_results.append(scan_entry)
    
    # Keep only last 1000 scans
    if len(scan_results) > 1000:
        scan_results.pop(0)
    
    logger.info(f"Scan result submitted: {scan_id} from {request.username}")
    
    return ScanResultResponse(
        success=True,
        message="Scan result submitted successfully",
        scan_id=scan_id
    )

@app.get("/api/admin/scans", response_model=AdminScansResponse)
async def get_all_scans(
    session_token: str,
    limit: int = 50,
    offset: int = 0,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    Get all scan results (admin only)
    """
    # Verify API key first (for extra security)
    if x_api_key and API_KEY and x_api_key == API_KEY:
        # API key access - full access
        pass
    else:
        # Check session
        session = verify_session(session_token)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        if not session['is_admin']:
            raise HTTPException(status_code=403, detail="Admin access required")
    
    # Apply pagination
    total = len(scan_results)
    paginated = scan_results[-limit - offset:][:limit] if scan_results else []
    
    return AdminScansResponse(
        scans=paginated,
        total=total,
        timestamp=int(time.time())
    )

@app.get("/api/admin/users")
async def get_users(
    session_token: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    Get list of users (admin only)
    """
    # Verify API key first
    if x_api_key and API_KEY and x_api_key == API_KEY:
        # API key access
        pass
    else:
        # Check session
        session = verify_session(session_token)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        if not session['is_admin']:
            raise HTTPException(status_code=403, detail="Admin access required")
    
    # Return user list (without password hashes)
    user_list = []
    for username, data in USERS.items():
        user_list.append({
            'username': username,
            'is_admin': data['is_admin'],
            'created_at': data['created_at']
        })
    
    return {
        'users': user_list,
        'total': len(user_list),
        'timestamp': int(time.time())
    }

@app.get("/api/verify-signature")
async def verify_signature(
    signature: str,
    timestamp: int,
    nonce: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """Verify a token signature"""
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Recreate signature
    expected_payload = f"{DISCORD_TOKEN}:{timestamp}:3600:{nonce}"
    expected_signature = hmac.new(
        API_KEY.encode('utf-8'),
        expected_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison
    is_valid = hmac.compare_digest(signature, expected_signature)
    
    return {
        "valid": is_valid,
        "timestamp": int(time.time())
    }

# ==================== ERROR HANDLERS ====================
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "timestamp": int(time.time())}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "timestamp": int(time.time())}
    )

# ==================== STARTUP ====================
@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("R6X CYBERSCAN Backend Starting...")
    logger.info(f"Version: 4.0.0")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info(f"Token Configured: {bool(DISCORD_TOKEN)}")
    logger.info(f"API Key Configured: {bool(API_KEY)}")
    logger.info(f"Admin User Configured: {bool(ADMIN_USERNAME and ADMIN_PASSWORD_HASH)}")
    logger.info(f"Regular Users Configured: {len(USERS) - (1 if ADMIN_USERNAME else 0)}")
    
    if not DISCORD_TOKEN:
        logger.warning("⚠️ DISCORD_TOKEN not set!")
    if not API_KEY:
        logger.warning("⚠️ API_KEY not set!")
    if not ADMIN_PASSWORD_HASH:
        logger.warning("⚠️ ADMIN_PASSWORD_HASH not set!")
    
    logger.info("=" * 60)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port, reload=(ENVIRONMENT == "development"))

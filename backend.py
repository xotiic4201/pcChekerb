"""
R6XInspector Backend API - FastAPI Version with Supabase
Complete backend with token delivery, user authentication, registration, and Supabase database
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
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from supabase import create_client, Client
import postgrest.exceptions

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

# ==================== SUPABASE CONFIGURATION ====================
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')

# Initialize Supabase client
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Supabase: {e}")
else:
    logger.warning("⚠️ Supabase credentials not set. Running in limited mode.")

# ==================== CONFIGURATION ====================
# Get from environment variables
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
API_KEY = os.environ.get('API_KEY')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'production')

# Admin credentials (stored in environment variables as fallback)
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'xotiic')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')  # SHA256 hash of password

# Token cache
token_cache = {
    'token': None,
    'expires': 0,
    'signature': None,
    'timestamp': 0
}

# Session store (in production, use Redis, but we'll use Supabase for sessions too)
# For now, keep sessions in memory for speed
sessions: Dict[str, Dict] = {}

# Registration tokens (for email verification if needed)
registration_tokens: Dict[str, Dict] = {}

# ==================== DATABASE FUNCTIONS ====================

async def init_database():
    """Initialize database tables if they don't exist"""
    if not supabase:
        logger.error("Supabase not initialized")
        return False
    
    try:
        # Check if tables exist by trying to query them
        # Users table
        try:
            supabase.table('users').select('*').limit(1).execute()
            logger.info("✅ Users table exists")
        except Exception as e:
            logger.error(f"❌ Users table error: {e}")
            # Table might not exist - create it via SQL in Supabase dashboard
            # We'll handle table creation through Supabase migrations
            pass
        
        # Scans table
        try:
            supabase.table('scans').select('*').limit(1).execute()
            logger.info("✅ Scans table exists")
        except Exception as e:
            logger.error(f"❌ Scans table error: {e}")
            pass
        
        # Sessions table (optional - we're using memory for now)
        
        return True
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        return False

async def get_user_by_username(username: str) -> Optional[Dict]:
    """Get user from Supabase by username"""
    if not supabase:
        return None
    
    try:
        result = supabase.table('users').select('*').eq('username', username).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None
    except Exception as e:
        logger.error(f"Error getting user {username}: {e}")
        return None

async def get_user_by_email(email: str) -> Optional[Dict]:
    """Get user from Supabase by email"""
    if not supabase:
        return None
    
    try:
        result = supabase.table('users').select('*').eq('email', email).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None
    except Exception as e:
        logger.error(f"Error getting user by email {email}: {e}")
        return None

async def create_user(username: str, password_hash: str, email: Optional[str] = None, is_admin: bool = False) -> Optional[Dict]:
    """Create a new user in Supabase"""
    if not supabase:
        return None
    
    try:
        now = datetime.now().isoformat()
        user_data = {
            'username': username,
            'password_hash': password_hash,
            'email': email or '',
            'is_admin': is_admin,
            'created_at': now,
            'last_login': None,
            'registered_via': 'api',
            'is_active': True
        }
        
        result = supabase.table('users').insert(user_data).execute()
        if result.data and len(result.data) > 0:
            logger.info(f"User created in database: {username}")
            return result.data[0]
        return None
    except postgrest.exceptions.APIError as e:
        if 'duplicate key' in str(e).lower():
            logger.warning(f"User {username} already exists")
        else:
            logger.error(f"Error creating user {username}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error creating user {username}: {e}")
        return None

async def update_user_login(username: str):
    """Update user's last login time"""
    if not supabase:
        return False
    
    try:
        now = datetime.now().isoformat()
        supabase.table('users').update({'last_login': now}).eq('username', username).execute()
        return True
    except Exception as e:
        logger.error(f"Error updating login for {username}: {e}")
        return False

async def save_scan_result(username: str, computer_name: str, scan_data: Dict, timestamp: int) -> Optional[str]:
    """Save scan result to Supabase"""
    if not supabase:
        return None
    
    try:
        scan_id = secrets.token_hex(8)
        now = datetime.now().isoformat()
        
        scan_entry = {
            'scan_id': scan_id,
            'username': username,
            'computer_name': computer_name,
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp).isoformat(),
            'data': json.dumps(scan_data),  # Store as JSON string
            'created_at': now
        }
        
        result = supabase.table('scans').insert(scan_entry).execute()
        if result.data and len(result.data) > 0:
            logger.info(f"Scan saved to database: {scan_id} for {username}")
            return scan_id
        return None
    except Exception as e:
        logger.error(f"Error saving scan for {username}: {e}")
        return None

async def get_user_scans(username: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """Get scans for a specific user"""
    if not supabase:
        return []
    
    try:
        result = supabase.table('scans') \
            .select('*') \
            .eq('username', username) \
            .order('timestamp', desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()
        
        # Parse JSON data back to dict
        scans = []
        for scan in result.data:
            if 'data' in scan and isinstance(scan['data'], str):
                try:
                    scan['data'] = json.loads(scan['data'])
                except:
                    pass
            scans.append(scan)
        
        return scans
    except Exception as e:
        logger.error(f"Error getting scans for {username}: {e}")
        return []

async def get_all_scans(limit: int = 50, offset: int = 0) -> List[Dict]:
    """Get all scans (admin only)"""
    if not supabase:
        return []
    
    try:
        result = supabase.table('scans') \
            .select('*') \
            .order('timestamp', desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()
        
        # Parse JSON data back to dict
        scans = []
        for scan in result.data:
            if 'data' in scan and isinstance(scan['data'], str):
                try:
                    scan['data'] = json.loads(scan['data'])
                except:
                    pass
            scans.append(scan)
        
        return scans
    except Exception as e:
        logger.error(f"Error getting all scans: {e}")
        return []

async def get_total_scans_count() -> int:
    """Get total number of scans"""
    if not supabase:
        return 0
    
    try:
        result = supabase.table('scans').select('*', count='exact').execute()
        return result.count if hasattr(result, 'count') else len(result.data)
    except Exception as e:
        logger.error(f"Error getting scan count: {e}")
        return 0

async def get_total_users_count() -> int:
    """Get total number of users"""
    if not supabase:
        return 0
    
    try:
        result = supabase.table('users').select('*', count='exact').execute()
        return result.count if hasattr(result, 'count') else len(result.data)
    except Exception as e:
        logger.error(f"Error getting user count: {e}")
        return 0

async def get_all_users() -> List[Dict]:
    """Get all users (admin only)"""
    if not supabase:
        return []
    
    try:
        result = supabase.table('users').select('*').order('created_at', desc=True).execute()
        return result.data
    except Exception as e:
        logger.error(f"Error getting all users: {e}")
        return []

async def delete_user(username: str) -> bool:
    """Delete a user and their scans"""
    if not supabase:
        return False
    
    try:
        # Delete user's scans first (foreign key constraint)
        supabase.table('scans').delete().eq('username', username).execute()
        # Delete user
        supabase.table('users').delete().eq('username', username).execute()
        logger.info(f"User deleted from database: {username}")
        return True
    except Exception as e:
        logger.error(f"Error deleting user {username}: {e}")
        return False

async def create_admin_if_not_exists():
    """Create admin user if it doesn't exist"""
    if not supabase or not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
        return
    
    try:
        existing = await get_user_by_username(ADMIN_USERNAME)
        if not existing:
            await create_user(
                username=ADMIN_USERNAME,
                password_hash=ADMIN_PASSWORD_HASH,
                email=os.environ.get('ADMIN_EMAIL', 'admin@r6x.com'),
                is_admin=True
            )
            logger.info(f"Admin user created: {ADMIN_USERNAME}")
    except Exception as e:
        logger.error(f"Error creating admin: {e}")

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
    registration_enabled: bool
    database_connected: bool

class HealthResponse(BaseModel):
    status: str
    timestamp: int
    version: str
    database_connected: bool

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
    username: Optional[str] = None

class RegisterRequest(BaseModel):
    username: str
    password_hash: str
    email: Optional[str] = None
    token: str
    invite_code: Optional[str] = None

class RegisterResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    requires_verification: bool = False

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

class UserInfo(BaseModel):
    username: str
    is_admin: bool
    email: Optional[str]
    created_at: str
    last_login: Optional[str]
    registered_via: str
    is_active: bool

class UsersResponse(BaseModel):
    users: List[UserInfo]
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

def validate_username(username: str) -> bool:
    """Validate username format"""
    if len(username) < 3 or len(username) > 30:
        return False
    if not username[0].isalpha():
        return False
    if not all(c.isalnum() or c in '_-' for c in username):
        return False
    return True

def validate_password(password_hash: str) -> bool:
    """Validate password hash (basic check)"""
    return len(password_hash) == 64 and all(c in '0123456789abcdef' for c in password_hash.lower())

# ==================== API ENDPOINTS ====================

@app.get("/", response_model=StatusResponse)
async def root():
    """Root endpoint - shows API status"""
    cleanup_sessions()
    
    # Get user count from database
    users_count = 0
    if supabase:
        users_count = await get_total_users_count()
    
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "4.0.0",
        "users_configured": users_count,
        "registration_enabled": True,
        "database_connected": supabase is not None
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    # Test database connection
    db_connected = False
    if supabase:
        try:
            supabase.table('users').select('*').limit(1).execute()
            db_connected = True
        except:
            db_connected = False
    
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "version": "4.0.0",
        "database_connected": db_connected
    }

@app.get("/api/status", response_model=StatusResponse)
async def api_status():
    """API status endpoint"""
    cleanup_sessions()
    
    # Get user count from database
    users_count = 0
    if supabase:
        users_count = await get_total_users_count()
    
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "4.0.0",
        "users_configured": users_count,
        "registration_enabled": True,
        "database_connected": supabase is not None
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

@app.post("/api/register", response_model=RegisterResponse)
async def register(request: RegisterRequest):
    """
    Register a new user
    """
    logger.info(f"Registration attempt for username: {request.username}")
    
    # Verify token first
    if not request.token or request.token != DISCORD_TOKEN:
        logger.warning("Invalid token in registration request")
        return RegisterResponse(
            success=False,
            message="Invalid authentication token"
        )
    
    # Validate username
    if not validate_username(request.username):
        return RegisterResponse(
            success=False,
            message="Username must be 3-30 characters, start with a letter, and contain only letters, numbers, _ or -"
        )
    
    # Validate password hash
    if not validate_password(request.password_hash):
        return RegisterResponse(
            success=False,
            message="Invalid password format"
        )
    
    # Check if Supabase is available
    if not supabase:
        logger.error("Database not available")
        return RegisterResponse(
            success=False,
            message="Registration temporarily unavailable"
        )
    
    # Check if username already exists
    existing = await get_user_by_username(request.username)
    if existing:
        logger.warning(f"Username already exists: {request.username}")
        return RegisterResponse(
            success=False,
            message="Username already taken"
        )
    
    # Check if email already exists (if provided)
    if request.email:
        existing_email = await get_user_by_email(request.email)
        if existing_email:
            return RegisterResponse(
                success=False,
                message="Email already registered"
            )
    
    # Optional: Check invite code if required
    invite_required = os.environ.get('INVITE_REQUIRED', 'false').lower() == 'true'
    if invite_required:
        valid_invites = os.environ.get('VALID_INVITE_CODES', '').split(',')
        if not request.invite_code or request.invite_code not in valid_invites:
            return RegisterResponse(
                success=False,
                message="Valid invite code required"
            )
    
    # Create user in database
    user = await create_user(
        username=request.username,
        password_hash=request.password_hash,
        email=request.email,
        is_admin=False
    )
    
    if not user:
        return RegisterResponse(
            success=False,
            message="Failed to create user"
        )
    
    logger.info(f"User registered successfully: {request.username}")
    
    # Optional: Send verification email
    requires_verification = os.environ.get('EMAIL_VERIFICATION', 'false').lower() == 'true'
    
    return RegisterResponse(
        success=True,
        message="Registration successful",
        username=request.username,
        requires_verification=requires_verification
    )

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
    
    # Check if Supabase is available
    if not supabase:
        logger.error("Database not available")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Login temporarily unavailable"
        )
    
    # Get user from database
    user = await get_user_by_username(request.username)
    
    if not user:
        logger.warning(f"User not found: {request.username}")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Invalid username or password"
        )
    
    # Verify password
    if not hmac.compare_digest(request.password_hash, user['password_hash']):
        logger.warning(f"Invalid password for user: {request.username}")
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Invalid username or password"
        )
    
    # Check if user is active
    if not user.get('is_active', True):
        return LoginResponse(
            success=False,
            is_admin=False,
            message="Account is disabled"
        )
    
    # Update last login
    await update_user_login(request.username)
    
    # Create session
    session_token = create_session(request.username, user['is_admin'])
    
    logger.info(f"Login successful for user: {request.username} (admin: {user['is_admin']})")
    
    return LoginResponse(
        success=True,
        is_admin=user['is_admin'],
        message="Login successful",
        session_token=session_token,
        expires_in=86400,
        username=request.username
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
    
    # Verify username matches session
    if session['username'] != request.username:
        raise HTTPException(status_code=403, detail="Username mismatch")
    
    # Check if Supabase is available
    if not supabase:
        # Fallback to memory storage if database not available
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
        
        # Store in memory (temporary)
        if not hasattr(app, 'scan_results'):
            app.scan_results = []
        app.scan_results.append(scan_entry)
        
        # Keep only last 1000 scans
        if len(app.scan_results) > 1000:
            app.scan_results.pop(0)
        
        logger.info(f"Scan result saved in memory: {scan_id} from {request.username}")
        
        return ScanResultResponse(
            success=True,
            message="Scan result submitted successfully (memory)",
            scan_id=scan_id
        )
    
    # Save to database
    scan_id = await save_scan_result(
        username=request.username,
        computer_name=request.computer_name,
        scan_data=request.scan_data,
        timestamp=request.timestamp
    )
    
    if not scan_id:
        raise HTTPException(status_code=500, detail="Failed to save scan result")
    
    logger.info(f"Scan result saved to database: {scan_id} from {request.username}")
    
    return ScanResultResponse(
        success=True,
        message="Scan result submitted successfully",
        scan_id=scan_id
    )

@app.get("/api/user/scans")
async def get_user_scans_endpoint(
    session_token: str,
    limit: int = 50,
    offset: int = 0
):
    """
    Get scan results for current user
    """
    # Verify session
    session = verify_session(session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    # Check if Supabase is available
    if supabase:
        # Get from database
        scans = await get_user_scans(session['username'], limit, offset)
        total = len(await get_user_scans(session['username'], 10000, 0))  # Hacky way to get total
    else:
        # Get from memory
        if not hasattr(app, 'scan_results'):
            app.scan_results = []
        
        user_scans = [s for s in app.scan_results if s['username'] == session['username']]
        total = len(user_scans)
        scans = user_scans[offset:offset + limit]
    
    return {
        'scans': scans,
        'total': total,
        'timestamp': int(time.time())
    }

@app.get("/api/admin/scans", response_model=AdminScansResponse)
async def get_all_scans_endpoint(
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
    
    # Check if Supabase is available
    if supabase:
        # Get from database
        scans = await get_all_scans(limit, offset)
        total = await get_total_scans_count()
    else:
        # Get from memory
        if not hasattr(app, 'scan_results'):
            app.scan_results = []
        
        total = len(app.scan_results)
        scans = app.scan_results[offset:offset + limit]
    
    return AdminScansResponse(
        scans=scans,
        total=total,
        timestamp=int(time.time())
    )

@app.get("/api/admin/users", response_model=UsersResponse)
async def get_users_endpoint(
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
    
    # Check if Supabase is available
    if supabase:
        # Get from database
        users_data = await get_all_users()
        total = len(users_data)
        
        # Convert to UserInfo model
        users = []
        for user in users_data:
            users.append(UserInfo(
                username=user['username'],
                is_admin=user['is_admin'],
                email=user.get('email', ''),
                created_at=user['created_at'],
                last_login=user.get('last_login'),
                registered_via=user.get('registered_via', 'unknown'),
                is_active=user.get('is_active', True)
            ))
    else:
        # Return empty list if no database
        users = []
        total = 0
    
    return UsersResponse(
        users=users,
        total=total,
        timestamp=int(time.time())
    )

@app.delete("/api/admin/users/{username}")
async def delete_user_endpoint(
    username: str,
    session_token: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    Delete a user (admin only)
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
    
    # Check if Supabase is available
    if not supabase:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Get user from database
    user = await get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Don't allow deleting admin
    if user['is_admin']:
        raise HTTPException(status_code=403, detail="Cannot delete admin users")
    
    # Delete user
    success = await delete_user(username)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete user")
    
    # Delete associated sessions
    sessions_to_delete = [token for token, session_data in sessions.items() 
                         if session_data['username'] == username]
    for token in sessions_to_delete:
        del sessions[token]
    
    logger.info(f"User deleted: {username}")
    
    return {"success": True, "message": f"User {username} deleted"}

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
    logger.info(f"Supabase Configured: {bool(SUPABASE_URL and SUPABASE_KEY)}")
    
    if supabase:
        # Initialize database
        await init_database()
        # Create admin user if not exists
        await create_admin_if_not_exists()
        logger.info("✅ Database connected and initialized")
    else:
        logger.warning("⚠️ Running without database - data will not persist!")
    
    if not DISCORD_TOKEN:
        logger.warning("⚠️ DISCORD_TOKEN not set!")
    if not API_KEY:
        logger.warning("⚠️ API_KEY not set!")
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("⚠️ SUPABASE_URL or SUPABASE_KEY not set!")
    
    logger.info("=" * 60)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port, reload=(ENVIRONMENT == "development"))

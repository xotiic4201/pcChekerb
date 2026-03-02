"""
R6XInspector Backend API - FastAPI Version
Fixed signature verification
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
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="R6XInspector Backend API",
    description="Secure token delivery for R6XInspector desktop app",
    version="1.0.0"
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
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
API_KEY = os.environ.get('API_KEY')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')

# Token cache
token_cache = {
    'token': None,
    'expires': 0,
    'signature': None,
    'timestamp': 0
}

# ==================== MODELS ====================
class TokenResponse(BaseModel):
    token: str
    timestamp: int
    expires: int
    signature: str
    nonce: str  # Add nonce for extra security

class StatusResponse(BaseModel):
    online: bool
    token_configured: bool
    timestamp: int
    environment: str
    version: str

class HealthResponse(BaseModel):
    status: str
    timestamp: int
    version: str

# ==================== MIDDLEWARE ====================
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# ==================== API ENDPOINTS ====================

@app.get("/", response_model=StatusResponse)
async def root():
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "1.0.0"
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "version": "1.0.0"
    }

@app.get("/api/status", response_model=StatusResponse)
async def api_status():
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "1.0.0"
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
    nonce = secrets.token_hex(8)  # Random nonce for signature
    
    # Create signature using HMAC-SHA256
    # Include token, timestamp, expires, and nonce
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

@app.get("/api/verify")
async def verify_token(
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
    logger.info("=" * 50)
    logger.info("R6XInspector Backend Starting...")
    logger.info(f"Token Configured: {bool(DISCORD_TOKEN)}")
    logger.info(f"API Key Configured: {bool(API_KEY)}")
    if not DISCORD_TOKEN:
        logger.warning("⚠️ DISCORD_TOKEN not set!")
    if not API_KEY:
        logger.warning("⚠️ API_KEY not set!")
    logger.info("=" * 50)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port)

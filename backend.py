from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
import hashlib
import hmac
import time
import logging
from datetime import datetime
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
# Get from environment variables
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
API_KEY = os.environ.get('API_KEY')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')

# Token cache
token_cache = {
    'token': None,
    'expires': 0,
    'signature': None
}

# ==================== MODELS ====================
class TokenResponse(BaseModel):
    token: str
    timestamp: int
    expires: int
    signature: str

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
    """Add security headers to all responses"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ==================== API ENDPOINTS ====================

@app.get("/", response_model=StatusResponse)
async def root():
    """Root endpoint - API information"""
    logger.info("Root endpoint accessed")
    return {
        "online": True,
        "token_configured": bool(DISCORD_TOKEN),
        "timestamp": int(time.time()),
        "environment": ENVIRONMENT,
        "version": "1.0.0"
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for Render"""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "version": "1.0.0"
    }

@app.get("/api/status", response_model=StatusResponse)
async def api_status():
    """Get API status"""
    logger.debug("Status endpoint accessed")
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
    Get Discord bot token
    Requires valid API key in X-API-Key header
    """
    client_host = "unknown"  # Can't access request.client.host directly
    
    logger.info(f"Token request received")
    
    # Verify API key
    if not x_api_key:
        logger.warning(f"Token request missing API key")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Unauthorized",
                "message": "X-API-Key header is required",
                "timestamp": int(time.time())
            }
        )
    
    # Constant-time comparison to prevent timing attacks
    if not API_KEY or not hmac.compare_digest(x_api_key, API_KEY):
        logger.warning(f"Invalid API key attempt")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Unauthorized",
                "message": "Invalid API key",
                "timestamp": int(time.time())
            }
        )
    
    # Check if token is configured
    if not DISCORD_TOKEN:
        logger.error("Discord token not configured on server")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service Unavailable",
                "message": "Discord token not configured",
                "timestamp": int(time.time())
            }
        )
    
    # Check cache first
    current_time = time.time()
    if token_cache['token'] and current_time < token_cache['expires']:
        logger.info(f"Returning cached token")
        return {
            "token": token_cache['token'],
            "timestamp": int(current_time),
            "expires": int(token_cache['expires'] - current_time),
            "signature": token_cache['signature']
        }
    
    # Generate new token response
    timestamp = int(current_time)
    expires_in = 3600  # Token valid for 1 hour
    expires_at = timestamp + expires_in
    
    # Create signature
    signature_payload = f"{timestamp}{expires_in}{DISCORD_TOKEN[-10:]}"
    signature = hmac.new(
        (API_KEY or "").encode(),
        signature_payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Update cache
    token_cache['token'] = DISCORD_TOKEN
    token_cache['expires'] = expires_at
    token_cache['signature'] = signature
    
    logger.info(f"Token generated successfully")
    
    return {
        "token": DISCORD_TOKEN,
        "timestamp": timestamp,
        "expires": expires_in,
        "signature": signature
    }

@app.get("/api/verify/{signature}")
async def verify_token(signature: str, x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """
    Verify a token signature
    """
    if not x_api_key or not API_KEY or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Simple verification
    is_valid = len(signature) > 0  # Add actual verification logic if needed
    
    return {
        "valid": is_valid,
        "timestamp": int(time.time())
    }

# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) else {
            "error": "HTTP Error",
            "message": str(exc.detail),
            "timestamp": int(time.time())
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """General exception handler"""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred",
            "timestamp": int(time.time())
        }
    )

# ==================== STARTUP/SHUTDOWN EVENTS ====================

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info("=" * 50)
    logger.info("R6XInspector Backend Starting...")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info(f"Token Configured: {bool(DISCORD_TOKEN)}")
    logger.info(f"API Key Configured: {bool(API_KEY)}")
    
    if not DISCORD_TOKEN:
        logger.warning("⚠️ DISCORD_TOKEN not set! Token endpoint will fail.")
    if not API_KEY:
        logger.warning("⚠️ API_KEY not set! Authentication will fail.")
    
    logger.info("=" * 50)

@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    logger.info("R6XInspector Backend Shutting Down...")

# For running directly with uvicorn
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(
        "backend:app",
        host="0.0.0.0",
        port=port,
        reload=(ENVIRONMENT == "development"),
        log_level="info"
    )

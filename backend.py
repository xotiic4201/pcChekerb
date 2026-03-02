"""
R6XInspector Backend API - FastAPI Version
Secure token delivery service for R6XInspector desktop application
"""

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
import hashlib
import hmac
import time
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uvicorn

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
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CONFIGURATION ====================
# Get from environment variables
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
API_KEY = os.environ.get('API_KEY')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')
PORT = int(os.environ.get('PORT', 10000))

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

class ErrorResponse(BaseModel):
    error: str
    details: Optional[str] = None
    timestamp: int

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
    logger.info(f"Root endpoint accessed from {request.client.host}")
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
    client_host = request.client.host if hasattr(request, 'client') else 'unknown'
    logger.info(f"Token request from {client_host}")
    
    # Verify API key
    if not x_api_key:
        logger.warning(f"Token request missing API key from {client_host}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Unauthorized",
                "message": "X-API-Key header is required",
                "timestamp": int(time.time())
            }
        )
    
    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(x_api_key, API_KEY or ""):
        logger.warning(f"Invalid API key attempt from {client_host}")
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
        logger.info(f"Returning cached token for {client_host}")
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
    
    logger.info(f"Token generated successfully for {client_host}")
    
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
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Verify signature (simplified example)
    expected = hashlib.sha256(f"{API_KEY}{int(time.time())}".encode()).hexdigest()[:16]
    is_valid = hmac.compare_digest(signature[:16], expected)
    
    return {
        "valid": is_valid,
        "timestamp": int(time.time())
    }

@app.get("/api/stats")
async def get_stats(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """
    Get usage statistics (admin only)
    """
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # In a real app, you'd track this in a database
    return {
        "total_requests": 0,  # Implement tracking if needed
        "active_tokens": 1 if token_cache['token'] else 0,
        "server_time": int(time.time()),
        "uptime": "N/A"  # Implement if needed
    }

# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail.get("error", "HTTP Error"),
            "message": exc.detail.get("message", str(exc.detail)),
            "timestamp": exc.detail.get("timestamp", int(time.time()))
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
    logger.info(f"Port: {PORT}")
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

# ==================== MAIN ====================

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=PORT,
        reload=(ENVIRONMENT == "development"),
        log_level="info"
    )

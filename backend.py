import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',') if os.getenv('AUTHORIZED_USERS') else []
API_KEY = os.getenv('API_KEY', 'rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId')
RENDER_URL = os.getenv('RENDER_URL', 'https://r6x-cyberscan-api.onrender.com')

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

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="R6X CyberScan API", 
    version="1.0.0",
    description="API for R6X CyberScan Discord Bot"
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

class ScanData(BaseModel):
    scan_id: str
    user_id: str
    name: str
    timestamp: str
    files_scanned: int
    suspicious_count: int
    r6_count: int
    steam_count: int
    duration: float
    system_info: Optional[Dict[str, Any]] = {}
    threats: Optional[List[Dict[str, Any]]] = []

class ScanResponse(BaseModel):
    status: str
    message: str
    scan_id: Optional[str] = None

# ==================== DATA STORAGE ====================
active_scans = {}
scan_history = []
bot_start_time = datetime.now()

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "R6X CyberScan API",
        "version": "1.0.0",
        "status": "online",
        "endpoints": {
            "/health": "Health check",
            "/api/start-scan": "Start a new scan (returns bot token)",
            "/api/scan/complete": "Mark scan as complete",
            "/download-exe": "Download R6XScan.exe"
        }
    }

@app.post("/api/start-scan", response_model=StartScanResponse)
async def start_scan(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Start a new scan and return bot token and channel ID"""
    # Check API key
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    logger.info(f"Start scan requested for user_id: {user_id}")
    
    # Verify user is authorized
    if str(user_id) not in AUTHORIZED_USERS and AUTHORIZED_USERS != ['']:
        logger.warning(f"Unauthorized user attempt: {user_id}")
        raise HTTPException(status_code=403, detail="User not authorized")
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id[-8:]}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': user_id,
        'start_time': datetime.now(),
        'status': 'pending',
        'bot_token': TOKEN,
        'channel_id': CHANNEL_ID
    }
    
    logger.info(f"✅ Scan started: {scan_id} for user {user_id}")
    
    # Return the bot token and channel ID to the scanner
    return StartScanResponse(
        scan_id=scan_id,
        bot_token=TOKEN,
        channel_id=CHANNEL_ID,
        message="Scan started successfully. Bot will run locally."
    )

@app.post("/api/scan/complete")
async def scan_complete(scan_data: ScanData, x_api_key: Optional[str] = Header(None)):
    """Mark scan as complete and store results"""
    # Check API key
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = scan_data.scan_id
    user_id = scan_data.user_id
    
    logger.info(f"Scan complete received for scan_id: {scan_id}")
    
    # Check if scan exists
    if scan_id not in active_scans:
        logger.warning(f"Scan ID not found: {scan_id}")
        raise HTTPException(status_code=404, detail="Invalid or expired scan ID")
    
    # Verify user matches
    if active_scans[scan_id]['user_id'] != user_id:
        logger.warning(f"User mismatch for scan {scan_id}")
        raise HTTPException(status_code=403, detail="User mismatch")
    
    # Update scan status
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    active_scans[scan_id]['data'] = scan_data.dict()
    
    # Add to history (no global keyword needed here)
    scan_history.append({
        'scan_id': scan_id,
        'user_id': user_id,
        'completed_time': datetime.now().isoformat(),
        'files_scanned': scan_data.files_scanned,
        'suspicious_count': scan_data.suspicious_count,
        'r6_count': scan_data.r6_count,
        'steam_count': scan_data.steam_count,
        'duration': scan_data.duration
    })
    
    # Keep only last 100 scans in history
    while len(scan_history) > 100:
        scan_history.pop(0)
    
    logger.info(f"✅ Scan completed: {scan_id}")
    
    return ScanResponse(
        status='success',
        message='Scan marked as complete',
        scan_id=scan_id
    )

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
    return {
        'status': 'healthy',
        'active_scans': len(active_scans),
        'total_scans_completed': len(scan_history),
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
    
    if scan_id in active_scans:
        scan = active_scans[scan_id]
        return {
            'scan_id': scan_id,
            'user_id': scan['user_id'],
            'status': scan['status'],
            'start_time': scan['start_time'].isoformat(),
            'completed_time': scan.get('completed_time', '').isoformat() if scan.get('completed_time') else None
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
                'duration': scan['duration']
            }
    
    raise HTTPException(status_code=404, detail="Scan ID not found")

@app.get("/api/scans/recent")
async def get_recent_scans(limit: int = 10, x_api_key: Optional[str] = Header(None)):
    """Get recent completed scans"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    recent = scan_history[-limit:] if scan_history else []
    return {'recent_scans': recent}

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
    
    return {
        'total_scans': total_scans,
        'total_files_scanned': total_files,
        'total_suspicious_files': total_suspicious,
        'average_duration': avg_duration,
        'active_scans': len(active_scans)
    }

# ==================== RUN FASTAPI ====================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting R6X CyberScan API on port {port}")
    logger.info(f"Authorized users: {AUTHORIZED_USERS}")
    logger.info(f"Channel ID: {CHANNEL_ID}")
    logger.info(f"Token loaded: {bool(TOKEN)}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)

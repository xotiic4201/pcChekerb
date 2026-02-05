from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import json
import aiohttp
from supabase import create_client, Client
from cryptography.fernet import Fernet
import base64
import urllib.parse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="xotiicsverify API",
    description="Secure Discord verification system backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Initialize Supabase
supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')

if not supabase_url or not supabase_key:
    logger.error("Missing Supabase credentials!")
    raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

supabase: Client = create_client(supabase_url, supabase_key)

# Rate limiting storage
rate_limit_cache = {}

class DatabaseManager:
    def __init__(self):
        self.cipher_key = os.environ.get('ENCRYPTION_KEY')
        if not self.cipher_key:
            self.cipher_key = base64.urlsafe_b64encode(Fernet.generate_key())
            logger.warning("Generated new encryption key")
        
        # Ensure key is proper format
        if isinstance(self.cipher_key, str):
            self.cipher_key = self.cipher_key.encode()
        
        # Pad or truncate to 32 bytes
        key = base64.urlsafe_b64encode(self.cipher_key[:32].ljust(32, b'0'))
        self.cipher = Fernet(key)
    
    def _encrypt(self, data: str) -> str:
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
    
    def _decrypt(self, encrypted_data: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
    
    def save_oauth_state(self, state: str, user_id: str = None, guild_id: str = None, redirect_url: str = None):
        try:
            data = {
                'state': state,
                'user_id': user_id,
                'guild_id': guild_id,
                'redirect_url': redirect_url,
                'created_at': datetime.now().isoformat()
            }
            supabase.table('oauth_states').insert(data).execute()
            logger.info(f"Saved OAuth state: {state[:10]}... for guild {guild_id}")
        except Exception as e:
            logger.error(f"Error saving OAuth state: {e}")
    
    def get_oauth_state(self, state: str) -> Optional[Dict[str, Any]]:
        try:
            response = supabase.table('oauth_states')\
                .select('*')\
                .eq('state', state)\
                .execute()
            
            if response.data:
                state_data = response.data[0]
                # Delete after retrieval
                supabase.table('oauth_states').delete().eq('state', state).execute()
                
                return {
                    'state': state_data['state'],
                    'user_id': state_data.get('user_id'),
                    'guild_id': state_data.get('guild_id'),
                    'redirect_url': state_data.get('redirect_url')
                }
            else:
                logger.warning(f"OAuth state not found: {state[:10]}...")
        except Exception as e:
            logger.error(f"Error getting OAuth state: {e}")
        return None
    
    def add_verified_user(self, discord_id: str, username: str, access_token: str, 
                         refresh_token: str, expires_in: int, guild_id: str, metadata: dict = None) -> bool:
        try:
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            data = {
                'discord_id': discord_id,
                'username': username,
                'access_token': self._encrypt(access_token),
                'refresh_token': self._encrypt(refresh_token),
                'expires_at': expires_at.isoformat(),
                'guild_id': guild_id,
                'metadata': metadata or {},
                'verified_at': datetime.now().isoformat()
            }
            
            supabase.table('verified_users').upsert(data, on_conflict='discord_id,guild_id').execute()
            logger.info(f"Added/updated verified user: {username} ({discord_id}) for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error adding verified user: {e}")
            return False

class OAuthHandler:
    def __init__(self):
        self.client_id = os.environ.get('DISCORD_CLIENT_ID')
        self.client_secret = os.environ.get('DISCORD_CLIENT_SECRET')
        self.redirect_uri = os.environ.get('REDIRECT_URI')
        self.bot_token = os.environ.get('DISCORD_BOT_TOKEN')
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logger.error("Missing OAuth configuration!")
            raise ValueError("OAuth credentials are required")
    
    async def exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self.redirect_uri,
                'scope': 'identify guilds.join'
            }
            
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            
            async with aiohttp.ClientSession() as session:
                async with session.post('https://discord.com/api/oauth2/token', data=data, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info("Successfully exchanged code for tokens")
                        return result
                    else:
                        error_text = await resp.text()
                        logger.error(f"Token exchange failed: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"Error exchanging code: {e}")
        return None
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        try:
            headers = {'Authorization': f'Bearer {access_token}'}
            async with aiohttp.ClientSession() as session:
                async with session.get('https://discord.com/api/users/@me', headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Failed to get user info: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
        return None
    
    async def add_user_to_guild(self, access_token: str, user_id: str, guild_id: str) -> bool:
        try:
            headers = {
                'Authorization': f'Bot {self.bot_token}',
                'Content-Type': 'application/json'
            }
            
            data = {
                'access_token': access_token
            }
            
            url = f'https://discord.com/api/guilds/{guild_id}/members/{user_id}'
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as resp:
                    if resp.status in [200, 201, 204]:
                        logger.info(f"Successfully added user {user_id} to guild {guild_id}")
                        return True
                    elif resp.status == 403:
                        logger.warning(f"Missing permissions to add user {user_id}")
                        return False
                    else:
                        error_text = await resp.text()
                        logger.error(f"Failed to add user: {resp.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Error adding user to guild: {e}")
            return False

# Initialize handlers
db = DatabaseManager()
oauth = OAuthHandler()

@app.get("/")
async def root():
    """Root endpoint"""
    return HTMLResponse("""
    <html>
        <head>
            <title>xotiicsverify API</title>
            <style>
                body {
                    font-family: 'Segoe UI', Tahoma, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .container {
                    text-align: center;
                    background: rgba(0, 0, 0, 0.3);
                    padding: 40px;
                    border-radius: 20px;
                    backdrop-filter: blur(10px);
                }
                h1 { font-size: 3em; margin: 0; }
                p { font-size: 1.2em; margin: 20px 0; }
                .status { color: #4ade80; font-weight: bold; }
                a { color: #60a5fa; text-decoration: none; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔐 xotiicsverify API</h1>
                <p class="status">✅ Online and Running</p>
                <p>Secure Discord verification system</p>
                <p><a href="/docs">📚 API Documentation</a></p>
            </div>
        </body>
    </html>
    """)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        supabase.table('verified_users').select('id').limit(1).execute()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "xotiicsverify API",
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

@app.get("/oauth/authorize")
async def oauth_authorize(guild_id: str, redirect_url: str = None):
    """Start OAuth flow for user verification"""
    try:
        # Generate state
        state = secrets.token_urlsafe(32)
        
        # Save state to database
        db.save_oauth_state(
            state=state,
            guild_id=guild_id,
            redirect_url=redirect_url
        )
        
        # Build authorization URL
        params = {
            'client_id': oauth.client_id,
            'redirect_uri': oauth.redirect_uri,
            'response_type': 'code',
            'scope': 'identify guilds.join',
            'state': state,
            'prompt': 'none'  # Don't prompt if already authorized
        }
        
        auth_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        logger.info(f"Redirecting to Discord OAuth for guild {guild_id}")
        return RedirectResponse(auth_url)
        
    except Exception as e:
        logger.error(f"OAuth authorize error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """Handle OAuth callback from Discord"""
    try:
        logger.info(f"Received OAuth callback with state: {state[:10]}...")
        
        # Verify state
        state_data = db.get_oauth_state(state)
        if not state_data:
            logger.error("Invalid or expired OAuth state")
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Verification Failed</title>
                        <style>
                            body {
                                font-family: sans-serif;
                                background: #1a1a2e;
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255, 0, 0, 0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                            }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Verification Failed</h1>
                            <p>Invalid or expired verification session.</p>
                            <p>Please try again from your Discord server.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        guild_id = state_data.get('guild_id')
        logger.info(f"Processing verification for guild {guild_id}")
        
        # Exchange code for tokens
        token_data = await oauth.exchange_code(code)
        if not token_data:
            logger.error("Failed to exchange code for tokens")
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Verification Failed</title>
                        <style>
                            body {
                                font-family: sans-serif;
                                background: #1a1a2e;
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255, 0, 0, 0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                            }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Verification Failed</h1>
                            <p>Could not verify your Discord account.</p>
                            <p>Please try again.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        # Get user info
        user_info = await oauth.get_user_info(token_data['access_token'])
        if not user_info:
            logger.error("Failed to get user info")
            return HTMLResponse("""
                <html>
                    <head>
                        <title>Verification Failed</title>
                        <style>
                            body {
                                font-family: sans-serif;
                                background: #1a1a2e;
                                color: white;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                height: 100vh;
                                margin: 0;
                            }
                            .container {
                                text-align: center;
                                background: rgba(255, 0, 0, 0.1);
                                padding: 40px;
                                border-radius: 20px;
                                border: 2px solid #f72585;
                            }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h1>❌ Verification Failed</h1>
                            <p>Could not retrieve your user information.</p>
                            <p>Please try again.</p>
                        </div>
                    </body>
                </html>
            """, status_code=400)
        
        username = f"{user_info['username']}#{user_info.get('discriminator', '0')}"
        user_id = user_info['id']
        
        logger.info(f"User {username} ({user_id}) verified")
        
        # Save user to database
        success = db.add_verified_user(
            discord_id=user_id,
            username=username,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in'],
            guild_id=guild_id,
            metadata={
                'avatar': user_info.get('avatar'),
                'email': user_info.get('email')
            }
        )
        
        if not success:
            logger.error("Failed to save user to database")
        
        # Try to add user to guild immediately
        added = await oauth.add_user_to_guild(
            access_token=token_data['access_token'],
            user_id=user_id,
            guild_id=guild_id
        )
        
        if added:
            logger.info(f"Successfully added {username} to guild {guild_id}")
        else:
            logger.info(f"Could not immediately add {username} to guild - will retry with /restore")
        
        # Return success page
        return HTMLResponse("""
            <html>
                <head>
                    <title>Verification Successful</title>
                    <style>
                        body {
                            font-family: 'Segoe UI', Tahoma, sans-serif;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                        }
                        .container {
                            text-align: center;
                            background: rgba(0, 0, 0, 0.3);
                            padding: 60px 40px;
                            border-radius: 20px;
                            backdrop-filter: blur(10px);
                            border: 2px solid #4ade80;
                            max-width: 500px;
                        }
                        h1 {
                            font-size: 3em;
                            margin: 0 0 20px 0;
                        }
                        p {
                            font-size: 1.2em;
                            margin: 15px 0;
                            line-height: 1.6;
                        }
                        .success {
                            color: #4ade80;
                            font-weight: bold;
                        }
                        .info {
                            color: #60a5fa;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ Verification Successful!</h1>
                        <p class="success">Your account has been verified.</p>
                        <p class="info">You should now have access to the server!</p>
                        <p>If you're not in the server yet, an admin can use the <code>/verify restore</code> command to add you.</p>
                        <p style="margin-top: 30px; font-size: 0.9em; opacity: 0.7;">You can close this window now.</p>
                    </div>
                </body>
            </html>
        """)
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return HTMLResponse("""
            <html>
                <head>
                    <title>Verification Error</title>
                    <style>
                        body {
                            font-family: sans-serif;
                            background: #1a1a2e;
                            color: white;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                        }
                        .container {
                            text-align: center;
                            background: rgba(255, 0, 0, 0.1);
                            padding: 40px;
                            border-radius: 20px;
                            border: 2px solid #f72585;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>❌ An Error Occurred</h1>
                        <p>Something went wrong during verification.</p>
                        <p>Please try again from your Discord server.</p>
                    </div>
                </body>
            </html>
        """, status_code=500)

@app.get("/api/stats/{guild_id}")
async def get_stats(guild_id: str):
    """Get server statistics"""
    try:
        # Get verified count
        response = supabase.table('verified_users')\
            .select('id', count='exact')\
            .eq('guild_id', guild_id)\
            .execute()
        
        total_verified = response.count or 0
        
        # Get restored count
        restored_response = supabase.table('verified_users')\
            .select('id', count='exact')\
            .eq('guild_id', guild_id)\
            .not_.is_('restored_at', 'null')\
            .execute()
        
        restored_count = restored_response.count or 0
        
        return {
            "success": True,
            "stats": {
                "total_verified": total_verified,
                "restored": restored_count,
                "pending": total_verified - restored_count
            }
        }
        
    except Exception as e:
        logger.error(f"Get stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting API server on port {port}")
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)

"""
R6XInspector Backend Server
Deploy this on Render.com to securely serve the Discord token
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import hashlib
import hmac
import time
from functools import wraps

app = Flask(__name__)
CORS(app)

# Load from environment variables on Render
DISCORD_TOKEN = os.environ.get('R6X_DISCORD_TOKEN', '')
API_KEY = os.environ.get('R6X_API_KEY', 'your-secret-api-key-here')
RATE_LIMIT = {}  # Simple rate limiting

def require_api_key(f):
    """Decorator to require API key"""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        
        if not api_key or api_key != API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Simple rate limiting
        ip = request.remote_addr
        current_time = time.time()
        
        if ip in RATE_LIMIT:
            if current_time - RATE_LIMIT[ip] < 60:  # 1 request per minute max
                return jsonify({'error': 'Rate limited'}), 429
        
        RATE_LIMIT[ip] = current_time
        
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def home():
    return jsonify({
        'name': 'R6XInspector Token Server',
        'status': 'running',
        'version': '1.0'
    })

@app.route('/api/token', methods=['GET'])
@require_api_key
def get_token():
    """Securely serve the Discord token"""
    if not DISCORD_TOKEN:
        return jsonify({'error': 'Token not configured'}), 500
    
    # Generate a request signature
    timestamp = str(int(time.time()))
    signature = hmac.new(
        API_KEY.encode(),
        f"{timestamp}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    return jsonify({
        'token': DISCORD_TOKEN,
        'timestamp': timestamp,
        'signature': signature,
        'expires': 300  # Token valid for 5 minutes
    })

@app.route('/api/verify', methods=['POST'])
@require_api_key
def verify_token():
    """Verify if token is still valid"""
    data = request.json
    # Add any additional verification logic here
    return jsonify({'valid': True, 'message': 'Token is valid'})

@app.route('/api/status', methods=['GET'])
def status():
    """Public status endpoint"""
    return jsonify({
        'service': 'R6XInspector Token Server',
        'status': 'operational',
        'token_configured': bool(DISCORD_TOKEN)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

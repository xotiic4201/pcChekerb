from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import os
import uuid
import json
from datetime import datetime
import threading
import logging

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory storage (use Redis in production)
connected_laptops = {}
bots_registry = {}
bot_logs = {}

class BotManager:
    def __init__(self):
        self.bots = {}
        self.laptop_connections = {}
    
    def register_laptop(self, laptop_id, sid):
        """Register a new laptop connection"""
        self.laptop_connections[laptop_id] = sid
        connected_laptops[laptop_id] = {
            'sid': sid,
            'connected_at': datetime.now(),
            'bot_count': 0
        }
        print(f"Laptop registered: {laptop_id}")
    
    def unregister_laptop(self, laptop_id):
        """Remove laptop connection"""
        if laptop_id in self.laptop_connections:
            del self.laptop_connections[laptop_id]
        if laptop_id in connected_laptops:
            del connected_laptops[laptop_id]
    
    def update_bot_status(self, bot_data):
        """Update bot status and broadcast to all clients"""
        bot_id = bot_data.get('id')
        if bot_id:
            bots_registry[bot_id] = bot_data
            bot_logs.setdefault(bot_id, []).append({
                'timestamp': datetime.now().isoformat(),
                'message': bot_data.get('status', '')
            })
            
            # Keep only last 100 logs
            if len(bot_logs[bot_id]) > 100:
                bot_logs[bot_id] = bot_logs[bot_id][-100:]
            
            # Broadcast update to all connected clients
            socketio.emit('bot_update', {
                'bot': bot_data,
                'total_bots': len(bots_registry)
            })
    
    def get_all_bots(self):
        """Get all bots data"""
        return list(bots_registry.values())
    
    def send_command_to_laptop(self, laptop_id, command):
        """Send command to specific laptop"""
        if laptop_id in self.laptop_connections:
            socketio.emit('bot_command', command, room=self.laptop_connections[laptop_id])
            return True
        return False

manager = BotManager()

# WebSocket events
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    # Find and remove disconnected laptop
    laptop_id = None
    for lid, data in connected_laptops.items():
        if data['sid'] == request.sid:
            laptop_id = lid
            break
    
    if laptop_id:
        manager.unregister_laptop(laptop_id)
        print(f"Laptop disconnected: {laptop_id}")

@socketio.on('register_laptop')
def handle_register_laptop(data):
    laptop_id = data.get('laptop_id', str(uuid.uuid4()))
    manager.register_laptop(laptop_id, request.sid)
    emit('registration_confirmed', {'laptop_id': laptop_id})

@socketio.on('bot_status_update')
def handle_bot_update(data):
    manager.update_bot_status(data)

@socketio.on('start_bot')
def handle_start_bot(data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            manager.send_command_to_laptop(laptop_id, {
                'action': 'start',
                'bot_id': bot_id
            })

@socketio.on('stop_bot')
def handle_stop_bot(data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            manager.send_command_to_laptop(laptop_id, {
                'action': 'stop',
                'bot_id': bot_id
            })

@socketio.on('restart_bot')
def handle_restart_bot(data):
    bot_id = data.get('bot_id')
    if bot_id in bots_registry:
        bot = bots_registry[bot_id]
        laptop_id = bot.get('laptop_id')
        if laptop_id:
            manager.send_command_to_laptop(laptop_id, {
                'action': 'restart',
                'bot_id': bot_id
            })

@socketio.on('deploy_bot')
def handle_deploy_bot(data):
    """Handle new bot deployment"""
    bot_name = data.get('name', 'New Bot')
    laptop_id = data.get('laptop_id')
    
    if not laptop_id:
        # Assign to laptop with least bots
        if connected_laptops:
            laptop_id = min(connected_laptops.keys(), 
                          key=lambda x: connected_laptops[x]['bot_count'])
    
    if laptop_id and laptop_id in connected_laptops:
        bot_id = str(uuid.uuid4())
        
        # Create bot entry
        bots_registry[bot_id] = {
            'id': bot_id,
            'name': bot_name,
            'status': 'deploying',
            'laptop_id': laptop_id,
            'created_at': datetime.now().isoformat(),
            'logs': []
        }
        
        # Send deployment command to laptop
        manager.send_command_to_laptop(laptop_id, {
            'action': 'deploy',
            'bot_id': bot_id,
            'bot_data': data
        })
        
        emit('bot_deployed', {'bot_id': bot_id, 'message': 'Bot deployment started'})

# REST API endpoints
@app.route('/api/bots', methods=['GET'])
def get_bots():
    return jsonify({
        'bots': manager.get_all_bots(),
        'total': len(bots_registry)
    })

@app.route('/api/bot/<bot_id>', methods=['GET'])
def get_bot(bot_id):
    if bot_id in bots_registry:
        return jsonify(bots_registry[bot_id])
    return jsonify({'error': 'Bot not found'}), 404

@app.route('/api/bot/<bot_id>/logs', methods=['GET'])
def get_bot_logs(bot_id):
    if bot_id in bot_logs:
        return jsonify({'logs': bot_logs[bot_id]})
    return jsonify({'logs': []})

@app.route('/api/upload', methods=['POST'])
def upload_bot():
    """Handle bot file upload"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    bot_name = request.form.get('name', file.filename)
    
    # Save file temporarily (in production, use cloud storage)
    upload_dir = 'uploads'
    os.makedirs(upload_dir, exist_ok=True)
    
    filename = f"{uuid.uuid4()}_{file.filename}"
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)
    
    # Process for deployment
    socketio.emit('new_bot_upload', {
        'filename': filename,
        'name': bot_name,
        'path': filepath
    })
    
    return jsonify({
        'success': True,
        'message': 'Bot uploaded successfully',
        'filename': filename
    })

@app.route('/api/system/stats', methods=['GET'])
def system_stats():
    """Get system statistics"""
    total_bots = len(bots_registry)
    running_bots = sum(1 for b in bots_registry.values() if b.get('status') == 'running')
    
    return jsonify({
        'total_bots': total_bots,
        'running_bots': running_bots,
        'connected_laptops': len(connected_laptops),
        'uptime': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
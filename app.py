import os
import json
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from loguru import logger
from trusted_spots_bot import TrustedSpotsBot

app = Flask(__name__)
app.config['SECRET_KEY'] = 'trusted_spots_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Global bot state
bot_instance = None
bot_thread = None
loop = None

# Custom Loguru sink to stream logs to WebSocket
class SocketIOSink:
    def write(self, message):
        # Only send important logs
        if "Zone Touch" in message or "RESULT" in message or "GOAL" in message or "PLACING" in message:
            socketio.emit('log_message', {'message': message.strip()})

logger.add(SocketIOSink(), format="{time:HH:mm:ss} | {level} | {message}")

def get_config():
    config_path = "config.json"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)

def bot_worker(config):
    global bot_instance, loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def on_update(data):
        socketio.emit('bot_update', data)

    bot_instance = TrustedSpotsBot(config, update_callback=on_update)

    try:
        loop.run_until_complete(bot_instance.initialize())
        loop.run_until_complete(bot_instance.monitor_and_trade())
    except Exception as e:
        logger.exception(f"Bot error: {e}")
    finally:
        loop.run_until_complete(bot_instance.stop())
        loop.close()
        bot_instance = None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'GET':
        return jsonify(get_config())
    else:
        new_config = request.json
        save_config(new_config)
        return jsonify({"status": "success"})

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "running": bot_instance is not None and not bot_instance.stop_event.is_set(),
        "kill_switch": bot_instance.kill_switch_active if bot_instance else False
    })

@app.route('/api/start', methods=['POST'])
def start_bot():
    global bot_thread
    if bot_instance is None:
        config = get_config()
        bot_thread = threading.Thread(target=bot_worker, args=(config,), daemon=True)
        bot_thread.start()
        return jsonify({"status": "started"})
    return jsonify({"status": "already running"})

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    if bot_instance:
        # Signal the bot to stop
        asyncio.run_coroutine_threadsafe(bot_instance.stop(), loop)
        return jsonify({"status": "stopping"})
    return jsonify({"status": "not running"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)

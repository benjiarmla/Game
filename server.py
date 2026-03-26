import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room, close_room
import string
import random
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'fallen_angel_secret!'
# Disable ping timeout disconnection quickly to prevent drops 
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_interval=10000, ping_timeout=5000)

MAX_PLAYERS_PER_ROOM = 100

# rooms = { room_code: { 'is_private': bool, 'host_sid': str, 'players': { sid: { state dict } } } }
rooms = {}

# We also keep track of what room a sid is in to easily handle disconnects
sid_to_room = {}

def generate_room_code(length=6):
    letters = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choice(letters) for i in range(length))
        if code not in rooms:
            return code

@app.route('/')
def index():
    return "The Fallen Angel Server Running."

@socketio.on('list_lobbies')
def handle_list_lobbies():
    public_lobbies = []
    for code, data in rooms.items():
        if not data['is_private']:
            host_name = "Unknown"
            if data['host_sid'] in data['players']:
                host_name = data['players'][data['host_sid']].get('name', 'Host')
            
            player_count = len(data['players'])
            public_lobbies.append({
                'code': code,
                'hostName': host_name,
                'currentPlayers': player_count,
                'maxPlayers': MAX_PLAYERS_PER_ROOM,
                'biomeStatus': data.get('biomeStatus', 'LOBBY')
            })
    emit('lobby_list', public_lobbies)

@socketio.on('create_lobby')
def handle_create_lobby(data):
    sid = request.sid
    is_private = data.get('is_private', False)
    player_name = data.get('name', 'Player')
    
    room_code = generate_room_code()
    
    rooms[room_code] = {
        'is_private': is_private,
        'host_sid': sid,
        'biomeStatus': 'LOBBY',
        'players': {},
        'boss_hp': {},
        'companion_target': sid
    }
    
    join_lobby_internal(sid, room_code, player_name)

@socketio.on('join_lobby')
def handle_join_lobby(data):
    sid = request.sid
    room_code = data.get('code', '').upper()
    player_name = data.get('name', 'Player')
    
    if room_code not in rooms:
        emit('lobby_error', {'message': 'Lobby not found.'})
        return
        
    if len(rooms[room_code]['players']) >= MAX_PLAYERS_PER_ROOM:
        emit('lobby_error', {'message': 'Lobby is full.'})
        return
        
    join_lobby_internal(sid, room_code, player_name)

def join_lobby_internal(sid, room_code, player_name):
    join_room(room_code)
    sid_to_room[sid] = room_code
    
    # Initialize player state
    is_host = (rooms[room_code]['host_sid'] == sid)
    player_state = {
        'id': sid,
        'name': player_name,
        'is_host': is_host,
        'is_ready': is_host, # host inherently ready
        'x': 0, 'y': 0,
        'hp': 100, 'maxHp': 100,
        'facing': 'down',
        'animFrame': 0,
        'inventory': [None]*35, # placeholder
        'gold': 0,
        'is_dead': False,
        'state': 'idle', # idle, walk, attack, dash
        'last_update': time.time(),
        'tintHue': random.randint(0, 360) # Assigned tint hue for distinguishability
    }
    
    rooms[room_code]['players'][sid] = player_state
    
    # Notify player they joined
    emit('lobby_joined', {
        'code': room_code,
        'is_host': is_host,
        'my_id': sid,
        'players': rooms[room_code]['players']
    })
    
    # Notify others in room
    emit('player_joined', player_state, room=room_code, skip_sid=sid)

@socketio.on('toggle_ready')
def handle_toggle_ready(is_ready):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    rooms[room_code]['players'][sid]['is_ready'] = bool(is_ready)
    emit('player_ready_changed', {'id': sid, 'is_ready': is_ready}, room=room_code)

@socketio.on('start_game')
def handle_start_game():
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    if rooms[room_code]['host_sid'] != sid:
        return # Only host can start
        
    rooms[room_code]['biomeStatus'] = 'PLAYING'
    emit('game_started', room=room_code)

@socketio.on('position_update')
def handle_position_update(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    player = rooms[room_code]['players'].get(sid)
    if not player: return
    
    now = time.time()
    # Rate limit (max ~60Hz)
    if now - player['last_update'] < 0.015:
        return
        
    # Validation: Speed Hack Check (simplified bounding box jump cap)
    # If they move > 20 tiles (1280px) in one update, reject it
    old_x, old_y = player['x'], player['y']
    new_x, new_y = data.get('x', old_x), data.get('y', old_y)
    
    # Skip speed check on the very first position update (player still at default 0,0)
    first_update = (old_x == 0 and old_y == 0 and not player.get('_has_position'))
    
    dist_sq = (new_x - old_x)**2 + (new_y - old_y)**2
    if not first_update and dist_sq > 1638400: # 1280^2
        # Reject update, force rubberband
        emit('force_position', {'x': old_x, 'y': old_y})
        return
    
    player['_has_position'] = True
        
    player['x'] = new_x
    player['y'] = new_y
    player['facing'] = data.get('facing', player['facing'])
    player['animFrame'] = data.get('animFrame', player['animFrame'])
    player['state'] = data.get('state', player['state'])
    player['last_update'] = now
    
    # We do NOT broadcast immediately to everyone. The background tick will batch it.
    
@socketio.on('inventory_update')
def handle_inventory_update(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    player = rooms[room_code]['players'].get(sid)
    if not player: return
    
    # In a real rigorous MMO, we would simulate ALL drops server side.
    # For this scale, we accept the authoritative inventory array from the client,
    # but we assert it matches known schemas and no absurd quantities exist.
    inventory = data.get('inventory', [])
    gold = data.get('gold', player['gold'])
    
    # Simplified validation: cap max stack sizes to 99, gold to 999999
    if gold > 999999: gold = 999999
    if gold < 0: gold = 0
    
    valid_inventory = []
    for item in inventory:
        if item is None:
            valid_inventory.append(None)
        elif isinstance(item, dict) and 'id' in item:
            # valid structure check
            qty = item.get('qty', 1)
            if qty > 999: qty = 999
            valid_inventory.append({'id': item['id'], 'qty': qty})
        else:
            valid_inventory.append(None)
            
    player['inventory'] = valid_inventory
    player['gold'] = gold
    # The server holds this so reconnects work
    
@socketio.on('boss_arena_enter')
def handle_boss_arena_enter(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    if rooms[room_code]['host_sid'] != sid: return # Host only
    
    boss_id = data.get('bossId')
    rooms[room_code]['boss_hp'][boss_id] = data.get('maxHp', 1000)
    emit('boss_teleport', {'bossId': boss_id}, room=room_code)

@socketio.on('boss_damage')
def handle_boss_damage(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    boss_id = data.get('bossId')
    damage = data.get('damage', 0)
    
    if boss_id in rooms[room_code]['boss_hp'] and damage > 0:
        rooms[room_code]['boss_hp'][boss_id] -= damage
        new_hp = rooms[room_code]['boss_hp'][boss_id]
        emit('boss_hp_update', {'bossId': boss_id, 'hp': new_hp}, room=room_code)
        
        if new_hp <= 0:
            del rooms[room_code]['boss_hp'][boss_id]
            emit('boss_defeated', {'bossId': boss_id}, room=room_code)

@socketio.on('player_death')
def handle_player_death():
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    player = rooms[room_code]['players'].get(sid)
    if player:
        player['is_dead'] = True
        player['hp'] = 0
        emit('player_died', {'id': sid}, room=room_code)

@socketio.on('begin_revive')
def handle_begin_revive(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    target_sid = data.get('targetId')
    if target_sid not in rooms[room_code]['players']: return
    
    reviver = rooms[room_code]['players'][sid]
    target = rooms[room_code]['players'][target_sid]
    
    # Validation: Check proximity (128x128 bounding box)
    if abs(reviver['x'] - target['x']) <= 128 and abs(reviver['y'] - target['y']) <= 128:
        if target['is_dead']:
            emit('revive_started', {'reviverId': sid, 'targetId': target_sid}, room=room_code)

@socketio.on('complete_revive')
def handle_complete_revive(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    
    target_sid = data.get('targetId')
    if target_sid not in rooms[room_code]['players']: return
    
    reviver = rooms[room_code]['players'][sid]
    target = rooms[room_code]['players'][target_sid]
    
    if abs(reviver['x'] - target['x']) <= 128 and abs(reviver['y'] - target['y']) <= 128:
        if target['is_dead']:
            target['is_dead'] = False
            target['hp'] = 3
            emit('revived_by', {'reviverName': reviver['name'], 'targetId': target_sid}, room=room_code)

@socketio.on('cutscene_control')
def handle_cutscene(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    if rooms[room_code]['host_sid'] == sid:
        # Host authorizes cutscene progress/start
        emit('sync_cutscene', data, room=room_code, skip_sid=sid)

@socketio.on('companion_assign')
def handle_companion(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    if rooms[room_code]['host_sid'] == sid:
        target_sid = data.get('targetId')
        if target_sid in rooms[room_code]['players']:
            rooms[room_code]['companion_target'] = target_sid
            emit('sync_companion', {'targetId': target_sid}, room=room_code)

@socketio.on('story_action')
def handle_story_action(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    # Relay to everyone else in the room
    emit('story_action', data, room=room_code, include_self=False)

@socketio.on('quest_action')
def handle_quest_action(data):
    sid = request.sid
    if sid not in sid_to_room: return
    room_code = sid_to_room[sid]
    # Relay to everyone else in the room
    emit('quest_action', data, room=room_code, include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sid_to_room:
        room_code = sid_to_room[sid]
        
        # We allow a small grace period for reconnects in a prod server,
        # but for simplicity we remove them and notify others immediately.
        if room_code in rooms and sid in rooms[room_code]['players']:
            del rooms[room_code]['players'][sid]
            emit('player_left', {'id': sid}, room=room_code)
            
            # If room is empty, clean it up
            if len(rooms[room_code]['players']) == 0:
                del rooms[room_code]
            elif rooms[room_code]['host_sid'] == sid:
                # Migrate host to random player
                new_host = list(rooms[room_code]['players'].keys())[0]
                rooms[room_code]['host_sid'] = new_host
                rooms[room_code]['players'][new_host]['is_host'] = True
                emit('host_migrated', {'newHostId': new_host}, room=room_code)
                
        leave_room(room_code)
        del sid_to_room[sid]

def background_tick():
    """ Runs at 20Hz (every 50ms) to broadcast state to all rooms """
    while True:
        eventlet.sleep(0.05)
        for room_code, room_data in dict(rooms).items():
            if not room_data['players']: continue
            
            # Build light payload
            payload = {}
            for sid, p in room_data['players'].items():
                payload[sid] = {
                    'x': p['x'], 'y': p['y'],
                    'facing': p['facing'],
                    'animFrame': p['animFrame'],
                    'state': p['state'],
                    'is_dead': p['is_dead'],
                    'name': p['name'], # Include for easy rendering
                    'tintHue': p['tintHue']
                }
                
            # Broadcast to everyone in room. 
            # (In highly optimized netcode we send deltas and use skip_sid, 
            # but for 100 players broadcasting the dict is extremely small ~20kb)
            socketio.emit('state_tick', payload, room=room_code)

if __name__ == '__main__':
    # Start background tick thread
    eventlet.spawn(background_tick)
    print("Starting Fallen Angel Multiplayer Server on port 5000...")
    socketio.run(app, host='0.0.0.0', port=5000)

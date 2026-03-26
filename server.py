# Multiplayer Synchronization Code

class MultiplayerSynchronization:
    def __init__(self):
        self.players = {}
        self.npc_states = {}
        self.inventory = {}
        self.boss_arena = None

    def broadcast_player_skin(self, player_id, skin_data):
        """ Broadcast player skin to all clients """
        print(f"Broadcasting skin for player {player_id}.")
        for client in self.players:
            client.send(skin_data)

    def sync_npc_state(self, npc_id, state_data):
        """ Sync NPC state across clients """
        self.npc_states[npc_id] = state_data
        print(f"NPC {npc_id} state updated.")

    def sync_inventory_pickup(self, player_id, item):
        """ Handle inventory pickup synchronization """
        print(f"Player {player_id} picked up {item}.")
        self.inventory[player_id].append(item)

    def teleport_to_boss_arena(self, player_id):
        """ Teleport player to the boss arena """
        print(f"Teleporting player {player_id} to boss arena.")
        self.boss_arena.teleport(player_id)

    def sync_hud_counters(self, player_id, counter_data):
        """ Sync HUD counters across clients """
        print(f"Syncing HUD counter for player {player_id}."), counter_data
        for client in self.players:
            client.update_hud(counter_data)

# Example Usage
multiplayer_sync = MultiplayerSynchronization()
multiplayer_sync.broadcast_player_skin('player123', 'skin_data')
multiplayer_sync.sync_npc_state('npc1', 'active')
multiplayer_sync.sync_inventory_pickup('player123', 'health_potion')
multiplayer_sync.teleport_to_boss_arena('player123')
multiplayer_sync.sync_hud_counters('player123', {'score': 100})
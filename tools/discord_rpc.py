#!/usr/bin/env python3
"""Discord Rich Presence bridge for iRx.

Uses Discord's local IPC protocol and app id 1534574125790658722 by default.
It watches a JSON state file so a launcher/game telemetry writer can update the
presence without bundling a third-party RPC library.
"""
import argparse
import json
import os
import socket
import struct
import sys
import time
import uuid
from pathlib import Path

DEFAULT_CLIENT_ID = '1534574125790658722'
OP_HANDSHAKE = 0
OP_FRAME = 1


def frame(opcode: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return struct.pack('<II', opcode, len(body)) + body


class DiscordIPC:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.conn = None

    def connect(self):
        self.close()
        if os.name == 'nt':
            last = None
            for i in range(10):
                path = rf'\\?\pipe\discord-ipc-{i}'
                try:
                    self.conn = open(path, 'r+b', buffering=0)
                    break
                except OSError as exc:
                    last = exc
            if not self.conn:
                raise OSError(f'Discord IPC pipe not found: {last}')
        else:
            roots = [os.getenv('XDG_RUNTIME_DIR'), os.getenv('TMPDIR'), '/tmp']
            last = None
            for root in filter(None, roots):
                for i in range(10):
                    path = os.path.join(root, f'discord-ipc-{i}')
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        s.connect(path)
                        self.conn = s
                        break
                    except OSError as exc:
                        last = exc
                        s.close()
                if self.conn:
                    break
            if not self.conn:
                raise OSError(f'Discord IPC socket not found: {last}')
        self.send_raw(frame(OP_HANDSHAKE, {'v': 1, 'client_id': self.client_id}))

    def send_raw(self, data: bytes):
        if hasattr(self.conn, 'sendall'):
            self.conn.sendall(data)
        else:
            self.conn.write(data)
            self.conn.flush()

    def set_activity(self, activity: dict):
        payload = {
            'cmd': 'SET_ACTIVITY',
            'args': {'pid': os.getpid(), 'activity': activity},
            'nonce': str(uuid.uuid4()),
        }
        self.send_raw(frame(OP_FRAME, payload))

    def clear(self):
        payload = {
            'cmd': 'SET_ACTIVITY',
            'args': {'pid': os.getpid(), 'activity': None},
            'nonce': str(uuid.uuid4()),
        }
        self.send_raw(frame(OP_FRAME, payload))

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None


def build_activity(state: dict, started: int) -> dict:
    mode = str(state.get('mode') or 'Playing')[:128]
    map_name = str(state.get('map') or 'Menu')[:128]
    server = str(state.get('server') or 'iRx')[:128]
    players = int(state.get('players') or 0)
    max_players = int(state.get('max_players') or 0)
    activity = {
        'details': f'{mode} • {map_name}',
        'state': server,
        'timestamps': {'start': int(state.get('started') or started)},
        'assets': {
            'large_image': str(state.get('large_image') or 'irx'),
            'large_text': str(state.get('large_text') or 'iRx'),
        },
        'instance': True,
    }
    if state.get('small_image'):
        activity['assets']['small_image'] = str(state['small_image'])
        activity['assets']['small_text'] = str(state.get('small_text') or mode)[:128]
    if players > 0 and max_players >= players:
        activity['party'] = {
            'id': str(state.get('party_id') or server)[:128],
            'size': [players, max_players],
        }
    buttons = []
    if state.get('join_url'):
        buttons.append({'label': 'Join iRx Server', 'url': str(state['join_url'])})
    if state.get('website'):
        buttons.append({'label': 'iRx', 'url': str(state['website'])})
    if buttons:
        activity['buttons'] = buttons[:2]
    return activity


def read_state(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--client-id', default=DEFAULT_CLIENT_ID)
    ap.add_argument('--state-file', type=Path, default=Path('saved/irx_presence.json'))
    ap.add_argument('--interval', type=float, default=5.0)
    ap.add_argument('--once', action='store_true')
    args = ap.parse_args()

    started = int(time.time())
    rpc = DiscordIPC(args.client_id)
    last_serialized = None
    while True:
        try:
            if not rpc.conn:
                rpc.connect()
            state = read_state(args.state_file)
            if not state:
                state = {
                    'mode': 'In Menu',
                    'map': 'Main Menu',
                    'server': 'iRx • irautox.ir:7575',
                }
            activity = build_activity(state, started)
            serial = json.dumps(activity, sort_keys=True)
            if serial != last_serialized:
                rpc.set_activity(activity)
                last_serialized = serial
                print(f'RPC updated: {activity["details"]} / {activity["state"]}')
            if args.once:
                break
            time.sleep(max(args.interval, 1.0))
        except KeyboardInterrupt:
            try:
                if rpc.conn:
                    rpc.clear()
            finally:
                rpc.close()
            break
        except (OSError, BrokenPipeError) as exc:
            rpc.close()
            last_serialized = None
            print(f'RPC reconnect: {exc}', file=sys.stderr)
            if args.once:
                raise
            time.sleep(3)


if __name__ == '__main__':
    main()

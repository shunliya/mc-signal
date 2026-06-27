"""
我的世界 P2P 链接器 - 信令服务器

功能:
  - 管理房间，配对 Host 和 Join 客户端
  - 交换双方的公网 UDP 地址以协助打洞
  - 可选的中继模式(打洞失败时通过服务器转发数据)

部署方式:
  1. 本地测试: python signal_server.py
  2. 部署到 Railway/Render 等免费平台
  3. 部署到自己的 VPS

协议:
  WebSocket + JSON 消息
"""

import asyncio
import json
import time
import secrets
from typing import Dict, Set, Optional
import websockets
from websockets.server import WebSocketServerProtocol


# ═══════════════════════════════════════════════════════════════
# 房间管理
# ═══════════════════════════════════════════════════════════════

class Room:
    """一个联机房间"""

    def __init__(self, code: str, host_ws: WebSocketServerProtocol,
                 host_info: dict, enable_relay: bool = True):
        self.code = code
        self.host_ws = host_ws
        self.host_info = host_info       # Host 的公网地址等信息
        self.join_ws: Optional[WebSocketServerProtocol] = None
        self.join_info: Optional[dict] = None
        self.enable_relay = enable_relay
        self.created_at = time.time()
        self.state = "waiting"  # waiting | pairing | connected | closed

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'state': self.state,
            'host': self.host_info.get('public_addr', ''),
            'created_ago': round(time.time() - self.created_at),
        }


class RoomManager:
    """房间管理器"""

    def __init__(self):
        self._rooms: Dict[str, Room] = {}         # code → Room
        self._ws_to_room: Dict[WebSocketServerProtocol, Room] = {}

    def create_room(self, code: str, ws: WebSocketServerProtocol,
                    info: dict, enable_relay: bool = True) -> Room:
        """创建房间"""
        # 清理同 code 的旧房间
        if code in self._rooms:
            old = self._rooms[code]
            self._ws_to_room.pop(old.host_ws, None)
            if old.join_ws:
                self._ws_to_room.pop(old.join_ws, None)

        room = Room(code, ws, info, enable_relay)
        self._rooms[code] = room
        self._ws_to_room[ws] = room
        return room

    def get_room(self, code: str) -> Optional[Room]:
        """获取房间"""
        return self._rooms.get(code)

    def join_room(self, code: str, ws: WebSocketServerProtocol,
                  info: dict) -> Optional[Room]:
        """加入房间"""
        room = self._rooms.get(code)
        if not room:
            return None
        if room.join_ws is not None:
            return None  # 已有人加入

        room.join_ws = ws
        room.join_info = info
        room.state = "pairing"
        self._ws_to_room[ws] = room
        return room

    def get_peer_room(self, ws: WebSocketServerProtocol) -> Optional[Room]:
        """获取此连接所属的房间"""
        return self._ws_to_room.get(ws)

    def remove_room(self, code: str):
        """移除房间"""
        room = self._rooms.pop(code, None)
        if room:
            self._ws_to_room.pop(room.host_ws, None)
            if room.join_ws:
                self._ws_to_room.pop(room.join_ws, None)

    def remove_client(self, ws: WebSocketServerProtocol):
        """移除客户端"""
        room = self._ws_to_room.pop(ws, None)
        if room:
            if ws == room.host_ws:
                room.state = "closed"
            elif ws == room.join_ws:
                room.join_ws = None
                room.state = "waiting"
            # 没有活跃客户端时清理房间
            if room.host_ws is None or (
                room.host_ws.close_code is not None and
                (room.join_ws is None or room.join_ws.close_code is not None)
            ):
                self._rooms.pop(room.code, None)

    def list_rooms(self) -> list:
        """列出房间"""
        return [r.to_dict() for r in self._rooms.values()]

    def cleanup_expired(self, max_age: float = 600):
        """清理过期房间"""
        now = time.time()
        expired = [
            code for code, room in self._rooms.items()
            if now - room.created_at > max_age
        ]
        for code in expired:
            self.remove_room(code)


# ═══════════════════════════════════════════════════════════════
# WebSocket 信令服务器
# ═══════════════════════════════════════════════════════════════

class SignalServer:
    """信令服务器"""

    def __init__(self, host: str = '0.0.0.0', port: int = 9876,
                 enable_relay: bool = True):
        self._host = host
        self._port = port
        self._enable_relay = enable_relay
        self._rooms = RoomManager()
        self._server = None

    async def start(self):
        """启动服务器"""
        self._server = await websockets.serve(
            self._handle_client,
            self._host, self._port,
            ping_interval=20,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,  # 2MB 消息大小限制
        )
        # 尝试自动配置 Windows 防火墙
        self._configure_firewall()
        print(f"╔══════════════════════════════════════════╗")
        print(f"║   我的世界 P2P 信令服务器已启动          ║")
        print(f"║   地址: ws://{self._host}:{self._port}   ║")
        print(f"║   中继模式: {'开启' if self._enable_relay else '关闭'}   ║")
        print(f"╚══════════════════════════════════════════╝")

        # 定期清理过期房间
        asyncio.create_task(self._cleanup_loop())

        await self._server.wait_closed()

    def _configure_firewall(self):
        """尝试自动添加 Windows 防火墙规则"""
        import subprocess
        rule_name = 'MC Linker Signal Server'
        try:
            # 检查是否已有规则
            result = subprocess.run(
                f'netsh advfirewall firewall show rule name="{rule_name}"',
                capture_output=True, text=True, shell=True, timeout=5
            )
            if '没有与指定标准相匹配的规则' in result.stdout or 'No rules match' in result.stdout:
                # 添加防火墙入站规则
                subprocess.run(
                    f'netsh advfirewall firewall add rule name="{rule_name}" '
                    f'dir=in action=allow protocol=TCP localport={self._port} '
                    f'description="我的世界P2P联机工具信令服务器"',
                    capture_output=True, shell=True, timeout=5
                )
                print(f"  [防火墙] 已添加 TCP {self._port} 入站规则")
        except Exception:
            pass  # 非 Windows 或权限不够则跳过

    async def _cleanup_loop(self):
        """定期清理过期房间"""
        while True:
            await asyncio.sleep(60)
            self._rooms.cleanup_expired()

    async def _handle_client(self, ws: WebSocketServerProtocol, path: str = '/'):
        """处理客户端连接"""
        peer = ws.remote_address
        print(f"[连接] {peer} 已连接")

        try:
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    await self._dispatch(ws, msg)
                except json.JSONDecodeError:
                    await self._send_error(ws, "无效的 JSON")
                except Exception as e:
                    await self._send_error(ws, str(e))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._rooms.remove_client(ws)
            print(f"[断开] {peer} 已断开")

    async def _dispatch(self, ws: WebSocketServerProtocol, msg: dict):
        """消息分发"""
        action = msg.get('action', '')

        if action == 'create':
            await self._handle_create(ws, msg)
        elif action == 'join':
            await self._handle_join(ws, msg)
        elif action == 'exchange':
            await self._handle_exchange(ws, msg)
        elif action == 'relay':
            await self._handle_relay(ws, msg)
        elif action == 'list':
            await self._handle_list(ws)
        elif action == 'ping':
            await self._send(ws, {'action': 'pong'})
        else:
            await self._send_error(ws, f"未知操作: {action}")

    async def _handle_create(self, ws: WebSocketServerProtocol, msg: dict):
        """创建房间"""
        code = msg.get('code', '')
        if not code:
            code = secrets.token_hex(3)[:6].upper()

        host_info = {
            'public_addr': msg.get('public_addr', ''),
            'local_addr': msg.get('local_addr', ''),
            'nat_type': msg.get('nat_type', ''),
            'mc_port': msg.get('mc_port', 25565),
            'client_id': msg.get('client_id', ''),
        }

        room = self._rooms.create_room(code, ws, host_info, self._enable_relay)
        print(f"[房间] {code} 已创建 (Host: {host_info['public_addr']})")

        await self._send(ws, {
            'action': 'created',
            'code': code,
            'message': '房间已创建，等待好友加入...',
        })

    async def _handle_join(self, ws: WebSocketServerProtocol, msg: dict):
        """加入房间"""
        code = msg.get('code', '').upper()
        if not code:
            await self._send_error(ws, "请输入房间码")
            return

        room = self._rooms.get_room(code)
        if not room:
            await self._send_error(ws, f"房间 {code} 不存在或已过期")
            return
        if room.join_ws is not None:
            await self._send_error(ws, f"房间 {code} 已满")
            return

        # 服务器观察到的客户端公网地址 (WebSocket TCP 连接, 比 UDP STUN 更可靠)
        observed_addr = f"{ws.remote_address[0]}:{ws.remote_address[1]}" if ws.remote_address else ''

        join_info = {
            'public_addr': msg.get('public_addr', ''),
            'local_addr': msg.get('local_addr', ''),
            'nat_type': msg.get('nat_type', ''),
            'client_id': msg.get('client_id', ''),
            'observed_addr': observed_addr,
        }

        room = self._rooms.join_room(code, ws, join_info)
        print(f"[房间] {code} Join 方已加入 (观察地址: {observed_addr})")

        # 通知 Join 方
        await self._send(ws, {
            'action': 'joined',
            'code': code,
            'host_info': {
                'public_addr': room.host_info['public_addr'],
                'local_addr': room.host_info.get('local_addr', ''),
                'nat_type': room.host_info.get('nat_type', ''),
                'mc_port': room.host_info.get('mc_port', 25565),
                'observed_addr': f"{room.host_ws.remote_address[0]}:{room.host_ws.remote_address[1]}" if room.host_ws.remote_address else '',
            },
            'message': f'已加入房间 {code}，开始建立连接...',
        })

        # 通知 Host 方
        await self._send(room.host_ws, {
            'action': 'peer_joined',
            'peer_info': {
                'public_addr': join_info['public_addr'],
                'local_addr': join_info.get('local_addr', ''),
                'nat_type': join_info.get('nat_type', ''),
                'observed_addr': join_info.get('observed_addr', ''),
            },
            'message': '好友已加入，开始打洞...',
        })

        room.state = "connected"

    async def _handle_exchange(self, ws: WebSocketServerProtocol, msg: dict):
        """转发消息给对方（打洞辅助）"""
        room = self._rooms.get_peer_room(ws)
        if not room:
            await self._send_error(ws, "未在房间中")
            return

        # 判断发送者
        if ws == room.host_ws and room.join_ws:
            target = room.join_ws
        elif ws == room.join_ws and room.host_ws:
            target = room.host_ws
        else:
            return

        await self._send(target, {
            'action': 'peer_msg',
            'data': msg.get('data', ''),
        })

    async def _handle_relay(self, ws: WebSocketServerProtocol, msg: dict):
        """中继数据转发"""
        if not self._enable_relay:
            await self._send_error(ws, "中继模式未启用")
            return

        room = self._rooms.get_peer_room(ws)
        if not room:
            return

        if ws == room.host_ws and room.join_ws:
            target = room.join_ws
        elif ws == room.join_ws and room.host_ws:
            target = room.host_ws
        else:
            return

        # 转发中继数据
        await self._send(target, {
            'action': 'relay_data',
            'data': msg.get('data', ''),
        })

    async def _handle_list(self, ws: WebSocketServerProtocol):
        """列出活跃房间"""
        rooms = self._rooms.list_rooms()
        await self._send(ws, {
            'action': 'room_list',
            'rooms': rooms,
        })

    async def _send(self, ws: WebSocketServerProtocol, msg: dict):
        """发送 JSON 消息"""
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _send_error(self, ws: WebSocketServerProtocol, error: str):
        """发送错误消息"""
        await self._send(ws, {'action': 'error', 'message': error})


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='我的世界 P2P 链接器 - 信令服务器'
    )
    parser.add_argument('--host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=9876, help='监听端口 (默认: 9876)')
    parser.add_argument('--no-relay', action='store_true', help='禁用中继模式')
    args = parser.parse_args()

    server = SignalServer(
        host=args.host,
        port=args.port,
        enable_relay=not args.no_relay,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\n服务器已停止")


if __name__ == '__main__':
    main()
